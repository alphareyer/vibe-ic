"""vibe-ic#2225 — the repair/retry loop re-dispatched its producer, not its readers.

WHAT WAS MEASURED, BEFORE ANY OF THIS WAS WRITTEN
=================================================
A real run's own `reports/orchestrator/phase2_one_shot.json`
(ve1903 final7 `Prob002_m2014_q4i`), step records in order:

    rtl_gen        BLOCKED  REFUSED TO RUN: phase1/extraction_patterns.json
    rtl_validate   BLOCKED  REFUSED TO RUN: phase2/stage1/rtl/*.sv OR *.v
                            (owed by step 1, read by step 2)
    sim            BLOCKED  REFUSED TO RUN: ...      (owed by step 1, read by 4)
    reference_tb   BLOCKED  rtl/ missing
    rtl_repair_retry_iter   RTL_REPAIR_RETRY 1/3
    rtl_gen        PASS     deterministic emit -> phase2/stage1/rtl/ EXISTS

and then `rtl_validate` and `sim` are never dispatched again. Their refusals
were true when they were recorded and false by the end of the run.

Re-measured against the LIVE pre-flight on a tree built for it (the shape of
`test_the_defect_the_two_sites_refuse_then_the_producer_produces` below): after
the RTL lands, `step_preflight.decide` returns READY for both sites, while the
run's own final record for both is still `BLOCKED / REQUIRED_INPUT_ABSENT` —
and `flow_dashboard_data._runner_verdict_overrides`, which already takes a
site's FINAL record, publishes that stale refusal as flow step 4's status.

ONE ROOT CAUSE. Not one per starved site: the loop re-dispatches its PRODUCER
outside `step_preflight.gate` and nothing revisits the sites that refused
BECAUSE that producer had not produced.

WHAT THESE TESTS PIN, AND WHAT THEY REFUSE TO PIN
=================================================
Every refusal below is built by CALLING `step_preflight.gate` on a real project
tree — never by hand-writing a `StepResult` that looks like one. A test that
constructs its own refusal proves the repair can read a shape the test invented;
these prove it can read the shape the flow actually emits.

They do NOT assert that the run's verdict turns green. `_aggregate_verdict`
counts a superseded BLOCKED as FAIL exactly as it counts `rtl_gen`'s today, and
a run that had to repair a starved producer is not a clean run. The claim is
narrower and is the issue's: the run's record for those sites must describe the
tree the run actually left behind.
"""
from __future__ import annotations

import ast
import inspect
import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as R      # noqa: E402
import flow_dashboard_data as _fdd      # noqa: E402
import step_preflight as _spf           # noqa: E402


# --------------------------------------------------------------------------- #
# Tree builders. Nothing here is a fixture standing in for an execution — these
# make the STATE, and the pre-flight is then really run against it.
# --------------------------------------------------------------------------- #
def _project(tmp_path: Path, *, rtl: bool, l_docs: bool = True) -> Path:
    proj = tmp_path / "proj"
    (proj / "phase2" / "stage1" / "rtl").mkdir(parents=True, exist_ok=True)
    docs = proj / "phase1" / "generated_docs"
    docs.mkdir(parents=True, exist_ok=True)
    if l_docs:
        for name in ("L10_TEST_CASES.json", "L12_BEHAVIORAL_SEQUENCES.json"):
            (docs / name).write_text("{}\n")
    if rtl:
        _write_rtl(proj)
    return proj


def _write_rtl(proj: Path) -> Path:
    p = proj / "phase2" / "stage1" / "rtl" / "chip_top.v"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("module chip_top(input clk); endmodule\n")
    return p


def _ran(*_a, **_k) -> R.StepResult:
    """A step that really executes. Its name is deliberately NOT the site's —
    `_spf.gate` returns the step's own object untouched, so a site row that
    carries this name proves the dispatch happened rather than the refusal."""
    return R.StepResult("dispatched", "PASS", 0.0, "the step actually ran")


def _dispatch(proj: Path, site: str, fn=_ran) -> R.StepResult:
    """EXACTLY what `main()` does at a pre-flighted site."""
    sr = _spf.gate(proj, "design_one_shot_runner", site,
                   R._preflight_refusal(site), fn, proj, "chip_top")
    # `main()` records the site's row under the SITE name.
    # R-0915-85 — a row re-published under the SITE name carries the
    # structured fields with it. Dropping `reason_class` here is how a
    # NOT_MEASURED row loses the reason its own producer named, which the row
    # type now refuses outright.
    return R.StepResult(site, sr.status, 0.0, sr.detail, extras=sr.extras,
                        reason_class=getattr(sr, "reason_class", ""),
                        declared_by=getattr(sr, "declared_by", ""),
                        disclosures=list(getattr(sr, "disclosures", ()) or ()))


def _registry(proj: Path, *sites: str) -> dict:
    return {s: (lambda s=s: [_dispatch(proj, s)]) for s in sites}


def _last(plan, name):
    return [s for s in plan if s.name == name][-1]


def _producer_ran() -> R.StepResult:
    """The row the repair/retry loop appends immediately before it calls the
    re-dispatch. It is REQUIRED in every plan below: `_redispatch_starved_sites`
    reads the producer's own last record and does nothing when the producer did
    not produce, so a plan without this row would make each test below pass for
    a reason it is not about."""
    return R.StepResult("rtl_gen", "PASS", 0.0, "deterministic emit")


# --------------------------------------------------------------------------- #
# 1. THE DEFECT, AND THE REPAIR, ON THE SAME TREE
# --------------------------------------------------------------------------- #
def test_the_defect_the_two_sites_refuse_then_the_producer_produces(tmp_path):
    """The reproduction. Both halves are measured, not asserted from the issue.

    Half one: with no RTL, the real pre-flight REFUSES both sites and charges
    the absence to flow step 1 — the step the retry loop re-dispatches.
    Half two: once the RTL is on disk, the SAME pre-flight returns READY for
    both. So the refusal rows are, by the end, statements about a tree that no
    longer exists — which is the issue in one sentence."""
    proj = _project(tmp_path, rtl=False)

    plan = [_dispatch(proj, "rtl_validate"), _dispatch(proj, "sim")]
    for sr in plan:
        assert sr.status == _spf.REFUSAL_STATUS, sr.detail
        assert sr.extras["finding"] == _spf.REFUSAL_FINDING
        assert "1" in R._refusal_producers(sr), sr.extras.get("absent_inputs")

    _write_rtl(proj)          # what the retry's step_rtl_gen does
    for site in ("rtl_validate", "sim"):
        assert _spf.decide(proj, "design_one_shot_runner", site).verdict \
            == "READY", "the refusal is stale — the input is on disk now"


def test_the_starved_sites_are_redispatched_and_their_final_record_is_current(
        tmp_path):
    proj = _project(tmp_path, rtl=False)
    plan = [_dispatch(proj, "rtl_validate"), _dispatch(proj, "sim")]
    stale_ids = {s.name: id(s) for s in plan}

    _write_rtl(proj)
    plan.append(_producer_ran())
    fresh = R._redispatch_starved_sites(
        plan, "rtl_gen", _registry(proj, "rtl_validate", "sim"))

    assert [f.name for f in fresh] == ["rtl_validate", "sim"], \
        "both starved sites, in the runner's own dispatch order"
    for site in ("rtl_validate", "sim"):
        final = _last(plan, site)
        assert id(final) != stale_ids[site], "a NEW record, not an edited one"
        assert final.status == "PASS"
        # the site was really dispatched: the step's own row came back
        assert final.extras.get("redispatched_after") == "rtl_gen"
        assert final.extras.get("finding") != _spf.REFUSAL_FINDING


def test_the_stale_row_keeps_its_status_and_is_marked_superseded(tmp_path):
    """Relabelling a refusal would be this same defect pointing the other way.
    The row keeps BLOCKED — what changes is that it now says it was answered."""
    proj = _project(tmp_path, rtl=False)
    plan = [_dispatch(proj, "rtl_validate")]
    stale = plan[0]

    _write_rtl(proj)
    plan.append(_producer_ran())
    R._redispatch_starved_sites(plan, "rtl_gen",
                                _registry(proj, "rtl_validate"))

    assert stale.status == _spf.REFUSAL_STATUS
    assert stale.extras["finding"] == _spf.REFUSAL_FINDING
    sup = stale.extras["superseded_by"]
    assert sup["producer_site"] == "rtl_gen" and sup["producer_steps"] == ["1"]
    assert sup["statuses"] == ["PASS"]


def test_the_published_step_status_stops_carrying_the_stale_refusal(tmp_path):
    """The consumer, not a restatement of the producer.

    `flow_dashboard_data._runner_verdict_overrides` joins a site's FINAL record
    onto the flow step it executes. Before the re-dispatch it publishes step 4
    as `fail` with a detail naming an absent RTL file that is on disk; after it,
    it has no failure verdict to join at all."""
    proj = _project(tmp_path, rtl=False)
    plan = [_dispatch(proj, "sim")]

    def _publish():
        odir = proj / "reports" / "orchestrator"
        odir.mkdir(parents=True, exist_ok=True)
        (odir / "phase2_one_shot.json").write_text(json.dumps(
            {"steps": [{"name": s.name, "status": s.status,
                        "detail": s.detail} for s in plan]}))
        return _fdd._runner_verdict_overrides(proj)

    before = _publish()
    assert before["4"]["runner_status"] == _spf.REFUSAL_STATUS
    assert "phase2/stage1/rtl" in before["4"]["detail"]

    _write_rtl(proj)
    plan.append(_producer_ran())
    R._redispatch_starved_sites(plan, "rtl_gen", _registry(proj, "sim"))
    assert "4" not in _publish(), \
        "step 4 is still published from the refusal the run superseded"


# --------------------------------------------------------------------------- #
# 2. WHAT MUST NOT BE TOUCHED — each of the issue's 驗收 bullets, measured
# --------------------------------------------------------------------------- #
def test_a_run_with_no_retry_is_unchanged(tmp_path):
    """No refusal to supersede means no dispatch and no row. The helper is only
    reachable from the retry path, and even there it is a no-op on a clean run."""
    proj = _project(tmp_path, rtl=True)
    plan = [_dispatch(proj, "rtl_validate"), _dispatch(proj, "sim"),
            _producer_ran()]
    assert [s.status for s in plan[:2]] == ["PASS", "PASS"]
    before = list(plan)

    called: list = []
    reg = {"rtl_validate": lambda: called.append("x") or [],
           "sim": lambda: called.append("x") or []}
    assert R._redispatch_starved_sites(plan, "rtl_gen", reg) == []
    assert plan == before and called == []


def test_a_deliberate_entry_or_exit_skip_is_not_resurrected(tmp_path):
    """`SKIPPED-BY-ENTRY` / `SKIPPED-BY-EXIT` are designed non-dispatches, not
    starvation. A retry that resurrected them would violate --entry-step."""
    proj = _project(tmp_path, rtl=True)
    plan = [R.StepResult("rtl_validate", "NOT_APPLICABLE", 0.0, "upstream", declared_by="the fixture declares this step not applicable"),
            R.StepResult("sim", "NOT_APPLICABLE", 0.0, "past the exit", declared_by="the fixture declares this step not applicable"),
            _producer_ran()]
    called: list = []
    reg = {s: (lambda: called.append(s) or []) for s in ("rtl_validate", "sim")}
    assert R._redispatch_starved_sites(plan, "rtl_gen", reg) == []
    assert called == []


def test_a_refusal_charged_to_another_producer_is_left_alone(tmp_path):
    """The pairing is the refusal's OWN `absent_inputs[*].from`, not the site
    name. Here `sim` has its RTL and is refused only for D1's L-docs; an
    `rtl_gen` retry says nothing about D1 and must not claim to have answered
    it. The refusal is produced by the real pre-flight, so the discrimination
    is over the shape the flow emits."""
    proj = _project(tmp_path, rtl=True, l_docs=False)
    sr = _dispatch(proj, "sim")
    assert sr.status == _spf.REFUSAL_STATUS
    assert R._refusal_producers(sr) == {"D1"}, sr.detail

    plan = [sr, _producer_ran()]
    called: list = []
    assert R._redispatch_starved_sites(
        plan, "rtl_gen", {"sim": lambda: called.append("x") or []}) == []
    assert called == [] and plan == [sr, plan[1]]


def test_a_blocked_row_that_is_not_a_preflight_refusal_is_never_claimed(
        tmp_path):
    """`step_reference_tb` emits BLOCKED for its own reasons ("rtl/ missing")
    with no `finding` and no `absent_inputs`. Reading BLOCKED alone would make
    this repair claim to know how to re-dispatch a step whose refusal it cannot
    even attribute."""
    plan = [R.StepResult("rtl_validate", _spf.REFUSAL_STATUS, 0.0,
                         "rtl/ missing -- REFUSED TO RUN",
                         reason_class=_spf.REFUSAL_REASON_CLASS),
            _producer_ran()]
    assert R._refusal_producers(plan[0]) == set()
    assert R._redispatch_starved_sites(plan, "rtl_gen", {}) == []


def test_an_unregisterable_site_is_recorded_unmeasured_not_left_standing(
        tmp_path):
    """The issue's last bullet: if a re-dispatch is not possible, the stale
    record may not stand as the run's verdict. It is superseded by a row that
    says NOTHING IS KNOWN — never by a zero, and never by a green."""
    proj = _project(tmp_path, rtl=False)
    plan = [_dispatch(proj, "rtl_validate")]
    _write_rtl(proj)
    plan.append(_producer_ran())

    fresh = R._redispatch_starved_sites(plan, "rtl_gen", {})   # empty registry
    assert [f.name for f in fresh] == ["rtl_validate"]
    row = _last(plan, "rtl_validate")
    assert row.status == _spf.REFUSAL_STATUS, "not green, and not a zero"
    assert row.extras["finding"] == R.SUPERSEDED_UNMEASURED
    assert "NOT re-measured" in row.detail
    assert R._aggregate_verdict([row]) == "NOT_MEASURED", (
        "an unmeasured supersession must not become a pass; the runner's own "
        "aggregator has to still fail on it")


def test_a_site_the_redispatch_repairs_is_not_revisited_again(tmp_path):
    """The bound on the extra dispatches, and it needs no counter: a site stops
    matching the moment its last record is no longer a refusal charged to this
    producer. A run that retries three times cannot re-dispatch a repaired site
    three times."""
    proj = _project(tmp_path, rtl=False)
    plan = [_dispatch(proj, "sim"), _producer_ran()]
    _write_rtl(proj)
    reg = _registry(proj, "sim")
    assert len(R._redispatch_starved_sites(plan, "sim_producer_absent", reg)) \
        == 0, "an unknown producer site has no span and re-dispatches nothing"
    assert len(R._redispatch_starved_sites(plan, "rtl_gen", reg)) == 1
    assert R._redispatch_starved_sites(plan, "rtl_gen", reg) == []


# --------------------------------------------------------------------------- #
# 3. THE WIRING — every retry re-dispatch of the producer is followed by it,
#    and the loop's own termination guard still measures the producer alone
# --------------------------------------------------------------------------- #
def _main_ast() -> ast.FunctionDef:
    tree = ast.parse(inspect.getsource(R))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("design_one_shot_runner.main not found")


def _call_name(node) -> str:
    """`f(...)` -> 'f'; `a.b(...)` -> 'b'; anything else -> ''."""
    if not isinstance(node, ast.Call):
        return ""
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _retry_reentries():
    """Every `plan.append(step_rtl_gen(...))` in `main()`, with the two
    statements that follow it in the SAME block."""
    out = []
    for node in ast.walk(_main_ast()):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if not isinstance(body, list):
                continue
            for i, stmt in enumerate(body):
                if (isinstance(stmt, ast.Expr)
                        and _call_name(stmt.value) == "append"
                        and stmt.value.args
                        and _call_name(stmt.value.args[0]) == "step_rtl_gen"):
                    out.append((stmt, body[i + 1:i + 3]))
    return out


def test_every_retry_reentry_is_followed_by_the_redispatch_after_its_digest():
    """The behavioural tests above prove the helper; this proves the loops call
    it, and that the ORDER at each site is the one the loop depends on.

    `#49 Fix 1` aborts the repair loop when a retry emits byte-identical RTL, so
    the digest has to be the PRODUCER's. A re-dispatched gate may legitimately
    write into `rtl/` (`test_a_redispatch_can_move_the_rtl_digest` measures that
    it can), and a digest taken after one would read as the emitter having made
    progress — an inert loop that never terminates. So at every re-entry the
    order is: producer, digest, re-dispatch.
    """
    sites = _retry_reentries()
    assert len(sites) >= 4, (
        f"only {len(sites)} retry re-entry site(s) of `step_rtl_gen` found in "
        "main(); the runner has two repair/retry loops, each with a plain and "
        "a hint-remediated re-dispatch")
    for stmt, following in sites:
        line = stmt.lineno
        assert len(following) == 2, f"line {line}: nothing follows the re-entry"
        digest, redispatch = following
        assert (isinstance(digest, ast.Assign)
                and _call_name(digest.value) == "_rtl_dir_sha256"), (
            f"line {line}: the statement after the re-entry is not the RTL "
            "digest the byte-identical-retry guard reads")
        assert (isinstance(redispatch, ast.Expr)
                and _call_name(redispatch.value)
                == "_redispatch_starved_sites"), (
            f"line {line}: this repair/retry re-dispatches `step_rtl_gen` and "
            "never revisits the sites that producer had starved — see "
            "vibe-ic#2225; their pre-retry refusals stay as the run's answer")
        assert digest.lineno < redispatch.lineno


def test_a_redispatch_can_move_the_rtl_digest(tmp_path):
    """Why the order above is load-bearing rather than tidy. If a re-dispatch
    could not touch `rtl/`, taking the digest after it would be harmless; it
    can, so it is not."""
    proj = _project(tmp_path, rtl=True)
    before = R._rtl_dir_sha256(proj)
    plan = [_dispatch(proj, "rtl_validate")]

    def _noisy():
        (proj / "phase2" / "stage1" / "rtl" / "extra.v").write_text(
            "module extra; endmodule\n")
        return [_dispatch(proj, "rtl_validate")]

    _noisy()
    assert R._rtl_dir_sha256(proj) not in (None, before)
    assert plan  # the plan is untouched by the digest question itself


def test_a_producer_that_was_itself_refused_starves_nothing_new(tmp_path):
    """A retry whose own `step_rtl_gen` was refused has produced nothing, so
    there is no new tree for a starved reader to be re-measured against. The
    producer's own last row is what decides — not the caller's optimism."""
    proj = _project(tmp_path, rtl=False)
    plan = [_dispatch(proj, "rtl_validate"), _dispatch(proj, "rtl_gen")]
    assert plan[1].status == _spf.REFUSAL_STATUS

    called: list = []
    assert R._redispatch_starved_sites(
        plan, "rtl_gen",
        {"rtl_validate": lambda: called.append("x") or []}) == []
    assert called == [] and _last(plan, "rtl_validate") is plan[0]


def test_every_site_dispatched_before_a_repair_loop_registers_its_redispatch():
    """A site can only be replayed if the dispatch registered how. Both sites
    that the reference_tb repair loop can starve — the ones dispatched BEFORE
    it — go through `_dispatch_site`, which registers as a side effect of
    dispatching so the two can never describe different calls."""
    src = inspect.getsource(R)
    for site in ("rtl_validate", "sim"):
        assert f'_dispatch_site("{site}"' in src, (
            f"site {site!r} is dispatched without registering a re-dispatch; "
            "after an RTL repair/retry its refusal can only be superseded by "
            "SUPERSEDED_UNMEASURED, never re-measured")
