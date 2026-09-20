"""The DFT steps 11/12/13 cannot name a commercial tool as their blocker.

OWNER DIRECTIVE 2026-09-16, verbatim: "一條開源工具限制 -> what tool? we can fix
all forked tool, so this should be fixed" (RULING R-0915-57).

MEASURED at main bac74aff0 on the shape of SPM run32, which published
`PASS_WITH_OPEN_SOURCE_CONSTRAINTS` deferring

    12: "Post-DFT optimisation (Design Compiler + DFT)"

The chain, every link read off this tree:

  1. The design's own L20 declared NO DFT (`dft_present:false, scan_chains:[],
     bist_mbist:[], jtag_tap:null`), so step 11 stood down on the
     `l_doc_declares` condition it already carried -- correctly, citing L20.
  2. Step 12 therefore had no scan netlist, and `design_one_shot_runner` wrote
     `phase2/stage2/synth/post_dft_not_run.json` carrying
     `capability_flag: cap:post_dft_scan_optimization` -- a claim that the
     OPEN-SOURCE CONTAINER lacks a capability.
  3. `check_step`'s #675 strict sibling promotion honoured the registered flag
     and set `self_skip_disclosed`.
  4. Step id 12 was in `_OPEN_SOURCE_CONTAINER_BLOCKED_STEPS`, so the step
     entered `oss_blocked_skipped`, the run went FAIL, and the FAIL was
     promoted back out to the deferral tier under a commercial tool's name.

No tool was missing. With no scan chain there is nothing to re-optimise, and
the post-DFT optimisation this flow actually performs is yosys -- run by the
runner itself, eight lines from the marker that claimed it was unavailable.
Design-declared absence had been recorded as a capability gap.

BOTH DIRECTIONS ARE CASES HERE, because a rule that only ever excuses is not a
rule. A design that declares no DFT gets a design-declared N/A citing L20; a
design that DECLARES DFT and produces no scan netlist gets a real MISSING, and
can no longer buy a deferral with any tool's name.

chip-AGNOSTIC: synthetic L20 documents in tmp_path, plus assertions over the
shipped flow yaml and the shipped constants.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

FLOW_YAML = PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"

#: The value each L20 field must carry for the design to have declared DFT
#: absent. Step 11 has always used this clause; step 12 now uses the same one.
ABSENT = {"dft_present": False, "scan_chains": [], "bist_mbist": [],
          "jtag_tap": None}

STEP12_OUTPUT = "phase2/stage2/synth/post_dft_netlist.v"


def _F():
    import flow_compliance_check as F  # noqa: PLC0415
    return F


def _R():
    import design_one_shot_runner as R  # noqa: PLC0415
    return R


def _steps():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(FLOW_YAML.read_text(errors="replace"))

    def walk(n):
        if isinstance(n, dict):
            if "id" in n and ("required_outputs" in n or "gate" in n):
                yield n
            for v in n.values():
                yield from walk(v)
        elif isinstance(n, list):
            for v in n:
                yield from walk(v)
    return {str(s["id"]): s for s in walk(doc)}


def _project(tmp_path, fields, marker=None, *, write_l20=True, body=None):
    """A project shaped like the run, with nothing in it but the declaration
    and the runner's own marker."""
    # R-0915-64 — A DESIGN THAT DECLARES NO DFT HAS INPUT DOCUMENTS THAT SAY
    # NOTHING ABOUT DFT, and this fixture used to have no input at all. Since
    # R-0915-64 the declarer requires the design's OWN INPUT to corroborate the
    # L-doc (documents scanned, terms searched, zero hits), because a GENERATED
    # skeleton's initialisers are not an input statement. An empty corpus is a
    # scan with no denominator and declares nothing, so without this the
    # fixture describes a project that cannot exist.
    #
    # ONLY THE FIXTURE MOVES. Every assertion below is untouched: these cases
    # exist to prove the L-DOC half decides, and they still do — the corpus
    # here is deliberately silent about DFT so the L20 fields remain the only
    # thing that varies between them.
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "L1_product_metadata.md").write_text(
        "# Part\nA small core that multiplies two numbers.\n",
        encoding="utf-8")
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    if write_l20:
        doc = body if body is not None else {
            "doc_id": "L20", "doc_name": "L20_DFT_SCAN_TOPOLOGY",
            "applicability": "APPLICABLE", "fields": dict(fields)}
        (gd / "L20_DFT_SCAN_TOPOLOGY.json").write_text(
            doc if isinstance(doc, str) else json.dumps(doc))
    synth = tmp_path / "phase2" / "stage2" / "synth"
    synth.mkdir(parents=True, exist_ok=True)
    if marker is not None:
        (synth / "post_dft_not_run.json").write_text(json.dumps(
            dict({"verdict": "SKIP", "reason": "(test marker)"}, **marker)))
    return tmp_path


def _is_oss_blocked(F, r) -> bool:
    """The exact predicate `main()` uses to build `oss_blocked_skipped`."""
    return (r.status == "NOT_APPLICABLE" and r.self_skip_disclosed
            and (r.id in F._OPEN_SOURCE_CONTAINER_BLOCKED_STEPS
                 or r.id in F._DFT_SIGNOFF_WITHDRAWN_STEPS))


# ── the three keys are gone, and the tier they left is still alive ────────

@pytest.mark.parametrize("sid", [11, 12, 13])
def test_no_DFT_step_names_a_commercial_tool_as_its_blocker(sid):
    F = _F()
    assert sid not in F._OPEN_SOURCE_CONTAINER_BLOCKED_STEPS, (
        f"step {sid} can still be deferred under a commercial tool's name: "
        f"{F._OPEN_SOURCE_CONTAINER_BLOCKED_STEPS.get(sid)!r} (R-0915-57)")


def test_the_deferral_tier_itself_is_kept_and_still_reachable():
    """R-0915-57 withdraws three entries, not the tier. Deleting a path that
    other producers still spell would be a different change."""
    F = _F()
    remaining = set(F._OPEN_SOURCE_CONTAINER_BLOCKED_STEPS)
    assert remaining, "the tier was emptied — that is not this ruling"
    assert {24, 25, 26, 28} <= remaining
    assert {"M1", "M2", "M3", "M4"} <= remaining
    assert {"A3", "A4", "A5", "A7", "A8", "A9"} <= remaining


# ── step 12 reads the design's own declaration, the same one step 11 reads ─

def test_step_12_carries_step_11s_own_L20_clause_byte_for_byte():
    steps = _steps()
    c11 = (steps["11"].get("condition") or {}).get("l_doc_declares")
    c12 = (steps["12"].get("condition") or {}).get("l_doc_declares")
    assert isinstance(c12, dict), "step 12 carries no l_doc_declares condition"
    assert c12 == c11, (
        "steps 11 and 12 stand down on the SAME declaration; if the clauses "
        "differ, one design can stand 11 down and still owe 12", c11, c12)
    assert c12.get("l_doc") == "L20"
    assert c12.get("all_absent") == ABSENT
    assert steps["12"].get("condition_kind") == "design_dependent"


def test_step_12_still_declares_its_output():
    """The repair is a CONDITION, not a shrunken declaration."""
    outs = [str(o) for o in (_steps()["12"].get("required_outputs") or [])]
    assert outs == [STEP12_OUTPUT], outs


def test_the_runner_and_the_flow_agree_on_what_declares_absence():
    """Two predicates, one question. If they drift, the runner can withhold a
    capability claim for a design the flow still holds to its outputs."""
    R = _R()
    c12 = (_steps()["12"].get("condition") or {}).get("l_doc_declares")
    assert R._L20_DFT_ABSENT_FIELDS == c12["all_absent"]


# ── direction 1: the SPM case — declared absence, no commercial excuse ────

def test_a_design_declaring_no_DFT_gets_a_declared_NA_citing_L20(tmp_path):
    F = _F()
    R = _R()
    proj = _project(tmp_path, ABSENT, R._POST_DFT_SKIP_DECLARED)
    r = F.check_step(proj, _steps()["12"], {})
    assert r.status == "NOT_APPLICABLE"
    assert r.self_skip_disclosed is False
    assert not _is_oss_blocked(F, r)
    joined = " ".join(r.reasons)
    assert "design-declared NOT_APPLICABLE" in joined
    assert "L20_DFT_SCAN_TOPOLOGY.json" in joined, (
        "a declared N/A must cite the declaring document", joined)


def test_the_declared_marker_makes_no_capability_claim():
    """`gate_reason: l20_dft_contract` is the same key step 11's own
    `dft_atpg_not_run.json` writes for this identical cause."""
    R = _R()
    assert "capability_flag" not in R._POST_DFT_SKIP_DECLARED
    assert R._POST_DFT_SKIP_DECLARED["gate_reason"] == "l20_dft_contract"
    assert R._POST_DFT_SKIP_DECLARED["skips_required_output"] == STEP12_OUTPUT


def test_even_the_OLD_capability_marker_cannot_reopen_the_deferral(tmp_path):
    """Defence in depth: the condition is evaluated BEFORE the #675 sibling
    promotion, so a stale marker left by an older runner still reads as a
    design-declared N/A rather than a commercial-tool deferral."""
    F = _F()
    R = _R()
    proj = _project(tmp_path, ABSENT, R._POST_DFT_SKIP_OWN)
    r = F.check_step(proj, _steps()["12"], {})
    assert r.status == "NOT_APPLICABLE"
    assert r.self_skip_disclosed is False
    assert not _is_oss_blocked(F, r)


# ── direction 2: THE NEGATIVE CONTROL — a design that DOES declare DFT ────

@pytest.mark.parametrize("field,asserting", [
    ("dft_present", True),
    ("scan_chains", [{"name": "chain0", "length": 64}]),
    ("bist_mbist", [{"kind": "mbist"}]),
    ("jtag_tap", {"ir_width": 4}),
])
def test_a_design_that_declares_DFT_owes_the_netlist(tmp_path, field,
                                                     asserting):
    """One field asserting DFT anywhere in L20 makes step 12 LIVE, and an
    absent post-DFT netlist a real MISSING — with no exit through any tool's
    name."""
    F = _F()
    R = _R()
    fields = dict(ABSENT)
    fields[field] = asserting
    proj = _project(tmp_path, fields, R._POST_DFT_SKIP_OWN)
    r = F.check_step(proj, _steps()["12"], {})
    assert r.status == "FAIL", (
        "a design that declares DFT and produced no post-DFT netlist has an "
        "unmet requirement", r.status, r.reasons)
    assert not _is_oss_blocked(F, r)


@pytest.mark.parametrize("kw", [
    {"write_l20": False},
    {"body": "{ this is not json"},
])
def test_absence_of_a_declaration_is_not_a_declaration_of_absence(tmp_path,
                                                                  kw):
    """A phase 1 that fell over cannot excuse the step."""
    F = _F()
    R = _R()
    proj = _project(tmp_path, ABSENT, R._POST_DFT_SKIP_OWN, **kw)
    r = F.check_step(proj, _steps()["12"], {})
    assert r.status == "FAIL", (r.status, r.reasons)


@pytest.mark.parametrize("missing", sorted(ABSENT))
def test_a_partly_filled_L20_runs_step_12(tmp_path, missing):
    F = _F()
    R = _R()
    fields = {k: v for k, v in ABSENT.items() if k != missing}
    proj = _project(tmp_path, fields, R._POST_DFT_SKIP_OWN)
    r = F.check_step(proj, _steps()["12"], {})
    assert r.status == "FAIL", (r.status, r.reasons)


# ── the runner's own predicate, both directions ───────────────────────────

def test_runner_predicate_only_answers_yes_to_a_complete_declaration(tmp_path):
    R = _R()
    assert R.l20_declares_no_dft(_project(tmp_path / "a", ABSENT)) is True
    assert R.l20_declares_no_dft(
        _project(tmp_path / "b", dict(ABSENT, dft_present=True))) is False
    assert R.l20_declares_no_dft(
        _project(tmp_path / "c", ABSENT, write_l20=False)) is False
    assert R.l20_declares_no_dft(
        _project(tmp_path / "d", ABSENT, body="{ nope")) is False


# ── the withdrawn steps keep their VISIBILITY, and lose only the exit ─────

def test_a_withdrawn_step_is_visible_to_the_verdict():
    """Map membership decided both visibility and promotability. R-0915-57
    withdraws the promotion; dropping the visibility too would have made a
    real DFT gap cost-free, which is the relaxing direction."""
    F = _F()
    for sid in (11, 12, 13):
        assert sid in F._DFT_SIGNOFF_WITHDRAWN_STEPS, sid
        assert sid not in F._OPEN_SOURCE_CONTAINER_BLOCKED_STEPS, sid


def test_the_false_capability_flag_is_gone_from_the_registry():
    """`cap:post_dft_scan_optimization` claimed the open-source container
    could not re-optimise a scan netlist. The runner does exactly that with
    yosys whenever a scan netlist exists, so the claim was never true — and no
    producer emits the flag any more, so the entry is deleted rather than
    emptied and registry and producers stay in step."""
    F = _F()
    assert "cap:post_dft_scan_optimization" not in \
        F._DECLARED_CAPABILITY_GAP_FLAGS
    assert not F._is_declared_capability_gap("cap:post_dft_scan_optimization")


def test_no_producer_still_emits_the_removed_flag():
    """The registry test that scans producer sources would fail-closed on a
    stale literal; this states the same requirement from this ruling's side."""
    hits = []
    for src in PROGRAMS.glob("*.py"):
        for n, line in enumerate(src.read_text(errors="replace").splitlines(),
                                 1):
            if "cap:post_dft_scan_optimization" not in line:
                continue
            # The two files that record WHY it went keep saying its name in
            # prose. A comment is not an emission; a code line is.
            if line.lstrip().startswith("#"):
                continue
            hits.append(f"{src.name}:{n}: {line.strip()}")
    assert hits == [], (
        "the removed flag is still emitted or referenced in code", hits)
