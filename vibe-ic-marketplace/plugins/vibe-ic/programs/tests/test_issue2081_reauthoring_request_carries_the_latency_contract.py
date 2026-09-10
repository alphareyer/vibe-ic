"""vibe-ic#2081 — the re-authoring request's FOURTH field.

WHAT THE OWNER RULED IS STILL MISSING (comment of 2026-09-09 on #2081)
======================================================================
    "Defect 1's real half is still missing: routing an escalation is not the
     same as a PATH from a Phase-3 sign-off STA violation back to Phase-2
     authoring. … nothing yet carries a Phase-3 corner + slack + failing-path
     endpoints + THE LATENCY CONTRACT THE SPEC ALLOWS back into a re-authoring
     request."

REPRODUCED before anything was changed (lane is2081, 8HD-4, 2026-09-10, live
main 28d13f1616), by running `sta_architectural_residual_check` on #2081's own
numbers — SS setup -2.53 ns, 39 arcs, one buffer, PRUB 1.62 ns — with the design
declaring its cycle count in `phase1/generated_docs`:

    verdict FAIL, route step 1 (Spec-to-RTL, skill 'spec-to-rtl')
    reason: corner 'SS', _17630_ -> _17661_, slack -2.53, residual 0.91 …
    keys:   [… die_sizing_basis, paths, reason, reasons, route_step …]
    'cycles_per_block' in the record?  False
    'latency'          in the record?  False
    '66'               in the record?  True  <- the digits inside `_17661_`

Three of the four fields were there. The fourth was not, anywhere.

WHY THE ABSENT ONE IS THE DANGEROUS ONE. The request says the shortfall is "a
combinational depth the ARCHITECTURE implies", and the cheapest way for an author
to cut depth is to add a pipeline stage. On #2081's design that breaks the
observable 66-cycle READY contract: a design that closes timing and fails its
spec. Every remedy the owner measured on this very design was chosen to keep the
count — "Neither rewrite moves the cycle count."

WHAT THIS DOES NOT DO. It does not rule on whether the spec PERMITS moving the
count. On this design the L-documents contradict themselves about exactly that
(L2:62 states 66 cycles as observable behaviour; L2:89 lists the latency count as
not constrained — now #2168), and a gate that picked a side would assert a state
nobody measured. The sentence hands the author the declaration, names where it
came from, and says the permission is NOT_MEASURED here.

NEVER GREENER: verdict, route and exit code are identical with and without the
record — asserted below in both directions. Nothing that failed now passes; a
design that declares nothing reads exactly as it did before.

chip-AGNOSTIC: generic register names, a generic cell family and a neutral
cycle count; no vendor, SKU, PDK or design literal drives any assertion.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG_DIR))

import sta_architectural_residual_check as mod  # noqa: E402

PROG = PROG_DIR / "sta_architectural_residual_check.py"

_ARCS = "\n".join(
    f"   0.04    0.20    0.68   {10 + i:5.2f} v _1{i:04d}_/X (cell__maj3_4)"
    for i in range(37))

#: #2081's own shape: 39 arcs, ONE buffer, adverse skew 1.07, slack -2.53.
_ARCH = f"""=== SETUP corner: process=SS liberty=lib_ss ===
STA_BASIS: POST_ROUTE_SPEF
Startpoint: _17630_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: _17661_ (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

    Cap    Slew   Delay    Time   Description
                   6.37    6.37   clock network delay (propagated)
   0.05    0.38    1.06    7.43 ^ _17630_/Q (cell__dfxtp_2)
{_ARCS}
   0.01    0.15    0.55   99.00 v rebuffer1/X (cell__buf_1)
                  25.90   25.90   clock clk (rise edge)
                   5.30   31.20   clock network delay (propagated)
                          -2.53   slack (VIOLATED)
"""


def _project(tmp_path: Path, docs=None) -> Path:
    proj = tmp_path / "proj"
    sta = proj / "phase3" / "stage3" / "sta"
    sta.mkdir(parents=True)
    (sta / "sta_mcorner_ocv.rpt").write_text(_ARCH, encoding="utf-8")
    if docs is not None:
        gd = proj / "phase1" / "generated_docs"
        gd.mkdir(parents=True)
        for name, body in docs.items():
            gd.joinpath(name).write_text(
                body if isinstance(body, str) else json.dumps(body))
    return proj


def _run(proj: Path):
    out = proj / "residual.json"
    cp = subprocess.run([sys.executable, str(PROG), str(proj),
                         "--json", str(out)], capture_output=True, text=True)
    return cp.returncode, json.loads(out.read_text())


_DECLARED = {"L7_MICROARCH.json": {"fields": {
    "latency_cycles_per_block": 66,
    "latency_cycles_per_block_evidence": {
        "source": "input/docs/README.md", "line": 38,
        "matched_token": "66 cycles per block"}}}}


# ── the fourth field ───────────────────────────────────────────────────────

def test_the_request_carries_the_declared_cycle_count(tmp_path):
    """The headline: the count, the key it was declared under, and the file."""
    rc, rep = _run(_project(tmp_path, _DECLARED))
    assert rc == 1 and rep["verdict"] == "FAIL"

    lc = rep["latency_contract"]
    assert lc, "the re-authoring request carries no latency contract at all"
    assert lc["declared_cycles"] == [66]
    assert lc["agrees"] is True
    row = lc["declarations"][0]
    assert row["key"] == "latency_cycles_per_block"
    assert row["source"] == "L7_MICROARCH.json"
    assert row["evidence"]["line"] == 38, (
        "the producer's own provenance record was dropped, so the author "
        "cannot check the declaration")


def test_the_reason_an_author_reads_states_the_count_and_its_source(tmp_path):
    """The JSON is for a program; the reason line is what a human acts on."""
    _rc, rep = _run(_project(tmp_path, _DECLARED))
    assert "66 CYCLE(S) PER BLOCK" in rep["reason"]
    assert "L7_MICROARCH.json:latency_cycles_per_block=66" in rep["reason"]
    assert "pipeline stage" in rep["reason"], (
        "the request names a depth problem without naming the remedy that "
        "silently breaks the contract")


def test_the_request_refuses_to_rule_on_what_the_spec_permits(tmp_path):
    """#2168 is open precisely because the L-docs contradict each other here.
    This gate must hand over the declaration, not adjudicate it."""
    _rc, rep = _run(_project(tmp_path, _DECLARED))
    assert "NOT_MEASURED here" in rep["reason"]
    assert "Phase-1 question" in rep["reason"]
    # The DENIAL must be explicit — not merely the absence of a claim, or a
    # future edit could delete the sentence entirely and still pass here.
    assert "does NOT establish whether the spec permits" in rep["reason"]
    # ...and no AFFIRMATIVE ruling. Spelled as whole claims, because the denial
    # above necessarily contains the words it denies: a bare substring test for
    # "the spec permits" fails on the CORRECT sentence, which is how the first
    # draft of this test went red against a program that was behaving.
    for claim in ("the count may be moved", "the count is free to move",
                  "the latency contract is UNCONSTRAINED", "is UNCONSTRAINED",
                  "may move the count", "the spec permits moving"):
        assert claim not in rep["reason"], (
            f"the gate ruled on a permission it never measured: {claim!r}")


def test_two_documents_declaring_different_counts_is_NOT_MEASURED(tmp_path):
    """The state this design is actually in. Naming both and settling neither
    is the only honest answer; picking the smaller would authorise a rewrite
    the spec may forbid."""
    docs = dict(_DECLARED)
    docs["L5_ADI_SPEC.json"] = {"fields": {"throughput_cycles_per_block": 128}}
    _rc, rep = _run(_project(tmp_path, docs))

    lc = rep["latency_contract"]
    assert lc["agrees"] is False
    assert lc["declared_cycles"] == [66, 128]
    assert "NOT_MEASURED" in rep["reason"]
    assert "L5_ADI_SPEC.json:throughput_cycles_per_block=128" in rep["reason"]
    assert "L7_MICROARCH.json:latency_cycles_per_block=66" in rep["reason"]


# ── fails quiet in every direction it cannot read ──────────────────────────

@pytest.mark.parametrize("docs,label", [
    (None, "no generated_docs at all"),
    ({}, "an empty generated_docs"),
    ({"L7_MICROARCH.json": "{ not json"}, "an unreadable document"),
    ({"L7_MICROARCH.json": {"fields": {"latency_cycles_per_block": "sixty-six"}}},
     "a count that is not a number"),
    ({"L7_MICROARCH.json": {"fields": {"rounds_per_block": 66}}},
     "a spelling this version does not know"),
])
def test_an_unreadable_contract_adds_NOTHING(tmp_path, docs, label):
    """It can under-disclose, never mis-disclose. Silence must never be read as
    'the spec places no constraint' — the reason is byte-identical to the arm
    with no documents, and that is what makes the disclosure safe."""
    _rc, rep = _run(_project(tmp_path, docs))
    assert rep["latency_contract"] is None, label
    assert "CYCLE(S) PER BLOCK" not in rep["reason"], label
    assert "NOT_MEASURED here" not in rep["reason"], label


# ── CONTROLS: what must NOT have moved ─────────────────────────────────────

def test_CONTROL_verdict_route_and_rc_are_identical_with_and_without_it(
        tmp_path):
    """Disclosure, not a verdict. Without this, a future edit could start
    deciding the outcome from a field that is absent on most of the corpus."""
    rc_with, with_ = _run(_project(tmp_path / "a", _DECLARED))
    rc_without, without = _run(_project(tmp_path / "b", None))

    assert rc_with == rc_without == 1
    for key in ("verdict", "route_step", "route_step_name", "route_skill"):
        assert with_[key] == without[key], key
    assert [p["category"] for p in with_["paths"]] == \
        [p["category"] for p in without["paths"]]
    assert with_["architectural_paths"] == without["architectural_paths"]


def test_CONTROL_a_clean_design_reads_exactly_as_before(tmp_path):
    """No violating path means no re-authoring request to qualify, so the
    contract is not even read. A clean run must pay nothing for this."""
    proj = tmp_path / "clean"
    sta = proj / "phase3" / "stage3" / "sta"
    sta.mkdir(parents=True)
    (sta / "sta_mcorner_ocv.rpt").write_text(
        _ARCH.replace("-2.53   slack (VIOLATED)", " 1.20   slack (MET)"),
        encoding="utf-8")
    gd = proj / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    gd.joinpath("L7_MICROARCH.json").write_text(json.dumps(
        _DECLARED["L7_MICROARCH.json"]))

    rc, rep = _run(proj)
    assert rc == 0 and rep["verdict"] == "PASS"
    assert rep["latency_contract"] is None
    assert "CYCLE(S) PER BLOCK" not in rep["reason"]


def test_CONTROL_the_first_three_fields_are_still_there(tmp_path):
    """The request already carried corner, slack and endpoints. A change that
    added the fourth and dropped one of those would satisfy every test above."""
    _rc, rep = _run(_project(tmp_path, _DECLARED))
    assert "corner 'SS'" in rep["reason"]
    assert "_17630_ -> _17661_" in rep["reason"]
    assert "-2.53 ns" in rep["reason"]
    assert "step 1 (Spec-to-RTL, skill 'spec-to-rtl')" in rep["reason"]


def test_CONTROL_the_reader_is_a_key_set_not_a_substring_search(tmp_path):
    """`_latency_rows` matches declared keys exactly. Without this, widening it
    to a substring test would make any field whose name contains one of them a
    latency declaration."""
    rows = mod._latency_rows(
        {"fields": {"max_latency_cycles_per_block_estimate": 99}}, "L7.json")
    assert rows == []
    rows = mod._latency_rows({"latency_cycles": 66}, "L7.json")
    assert [r["key"] for r in rows] == ["latency_cycles"]
