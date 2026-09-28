"""A required STA corner is judged for the role the RUN declared it serves.

R-0915-126. The flow signs setup off at the slow process corner and hold at the
fast one, and it says so outright in its own stance artifact. Demanding a hold
reading from the slow corner is a fabricated violation, not a found one, so the
L24 corner half reads that declaration to learn what evidence is OWED and still
reads the declared audit's native bytes for the evidence itself.

Both directions are pinned here: the role-declared run closes, and every way of
removing the evidence -- absent declaration, a declared role with no reading,
the other role's reading standing in, a no-role corner with no analysis report,
a failing reading -- still refuses.
"""
import json
import pytest
import _audit_receipt as AR
import l24_signoff_evidence_backed_check as G

#: The sectioned post-route report the flow writes: one section per corner,
#: each naming the ROLE it serves, as the real reports do.
SECTION = ("=== {kind} corner: process={corner} liberty={corner}.lib, "
           "SPEF=design.{rc}.spef ===\n"
           "STA_BASIS: POST_ROUTE_SPEF\n"
           "STA_BASIS_LIBERTY: {corner}.lib\n"
           "STA_BASIS_NETLIST: design_pnr.v\n"
           "STA_BASIS_SPEF: design.{rc}.spef\n"
           "worst slack {rc} {slack}\n"
           "tns max 0.00\n"
           "wns {rc} {slack}\n")

PER_CORNER = ("Path Type: max\n"
              "worst slack max {slack}\n"
              "tns max 0.00\n"
              "wns max {slack}\n")


def fixture(tmp_path, change=None):
    p = tmp_path
    # Phase 3 reports must be newer than the netlist whose timing they
    # measured. FX_P2 correctly treats older reports as historical.
    netlist = p / "phase2/stage2/synth/netlist_yosys.v"
    netlist.parent.mkdir(parents=True, exist_ok=True)
    netlist.write_text("module chip; endmodule\n")
    native = p / "phase3/stage3/sta"
    native.mkdir(parents=True)
    docs = p / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L24_SIGNOFF.json").write_text(json.dumps({
        "doc_name": "L24_SIGNOFF",
        "fields": {"signoff_requirements": [{
            "check": "STA", "stated": True, "requirement": "met",
            "corners": ["SS", "TT", "FF"],
            "citation": {"document": "input/docs/spec.md", "line": 1}}]}}))

    # The run's OWN declaration of which corner serves which role.
    setup_slack, hold_slack = "6.36", "0.51"
    if change == "hold-violated":
        hold_slack = "-0.20"
    body = SECTION.format(kind="SETUP", corner="SS", rc="max", slack=setup_slack)
    if change == "ff-carries-setup-not-hold":
        body += SECTION.format(kind="SETUP", corner="FF", rc="max", slack=setup_slack)
    elif change != "no-ff-section":
        body += SECTION.format(kind="HOLD", corner="FF", rc="min", slack=hold_slack)
    report = native / "post_route_timing.rpt"
    report.write_text(body)

    stance = {"setup_process_corner": "SS", "hold_process_corner": "FF"}
    if change == "stance-names-other-corners":
        stance = {"setup_process_corner": "SF", "hold_process_corner": "FS"}
    if change != "no-stance":
        st = p / "reports/phase3"
        st.mkdir(parents=True, exist_ok=True)
        (st / "mcorner_ocv_stance.json").write_text(json.dumps(stance))

    # TT serves NEITHER sign-off role; its own per-corner report is the evidence
    # that it was analysed.
    if change != "no-tt-analysis":
        pc = native / "per_corner"
        pc.mkdir(parents=True)
        (pc / "sta_TT.rpt").write_text(PER_CORNER.format(
            slack="-0.30" if change == "tt-violated" else "0.00"))

    audit = {"program": "eda_report_audit:sta", "passed": True, "findings": [],
             "subject": AR.subject_of([report], relative_to=p)}
    out = p / "reports/phase3/sta/post_route_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(audit))
    receipt = p / "reports/orchestrator/phase3_one_shot.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({
        "verdict": "PASS",
        "phase2_synth": G._pl.phase2_synth_input_identity(p),
        "phase3_inputs": G._pl.phase3_signoff_input_identity(p),
    }))
    return p


def run(p):
    rec = p / "gate.json"
    rc = G.main(["l24", str(p), "--json", str(rec)])
    return rc, json.loads(rec.read_text())


def sta_row(rep):
    return next(r for r in rep["requirements"] if r["check"] == "STA")


def test_a_corner_reported_for_the_role_it_serves_closes_the_requirement(tmp_path):
    rc, rep = run(fixture(tmp_path))
    row = sta_row(rep)
    assert (rc, row["outcome"]) == (0, "BACKED"), row
    cov = row["corner_coverage"]
    assert cov["covered"] == ["FF", "SS", "TT"] and cov["missing"] == []
    assert cov["issues"] == []


def test_the_record_names_where_the_role_obligation_came_from(tmp_path):
    _rc, rep = run(fixture(tmp_path))
    cov = sta_row(rep)["corner_coverage"]
    assert cov["declared_corner_roles"] == {"SS": ["SETUP"], "FF": ["HOLD"]}
    assert cov["declared_corner_roles_source"] == "reports/phase3/mcorner_ocv_stance.json"
    assert [a["corner"] for a in cov["analysis_only"]] == ["TT"]
    assert cov["analysis_only"][0]["report"] == "phase3/stage3/sta/per_corner/sta_TT.rpt"


@pytest.mark.parametrize("case,unmet", [
    # No declaration of what each corner serves: the stricter both-roles demand
    # stands. An absent role map must never make coverage easier.
    ("no-stance", {"FF", "SS", "TT"}),
    ("stance-names-other-corners", {"FF", "SS", "TT"}),
    # A corner declared for a role with no reading of that role at all.
    ("no-ff-section", {"FF"}),
    # The OTHER role's reading does not discharge the declared one.
    ("ff-carries-setup-not-hold", {"FF"}),
    # A corner that serves no sign-off role still owes its analysis report.
    ("no-tt-analysis", {"TT"}),
    ("tt-violated", {"TT"}),
])
def test_removing_the_evidence_still_refuses(tmp_path, case, unmet):
    rc, rep = run(fixture(tmp_path, case))
    row = sta_row(rep)
    assert (rc, row["outcome"]) == (1, "UNMET_CORNER_EVIDENCE"), (case, row)
    assert unmet <= set(row["corner_coverage"]["missing"]), (case, row["corner_coverage"])


def test_a_declared_role_whose_reading_fails_is_still_a_violation(tmp_path):
    # FF is declared for HOLD and reports HOLD -- failing. Coverage is not the
    # question here; the failing reading must be surfaced, not absorbed.
    rc, rep = run(fixture(tmp_path, "hold-violated"))
    row = sta_row(rep)
    assert rc == 1 and row["outcome"] != "BACKED"
    assert any("FF" in i and "HOLD" in i for i in row["corner_coverage"]["issues"]), row
