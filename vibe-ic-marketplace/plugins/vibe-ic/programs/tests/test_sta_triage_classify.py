"""Unit tests for `sta_triage_classify.py`."""
import importlib
import json
import sys

from _shipped_version import shipped_plugin_version  # noqa: E402  (#800)
mod = importlib.import_module("sta_triage_classify")


def boundary(**updates):
    record = {"schema": "vibeic.sta_constraint_boundary.v1",
              "source_sha256": "a" * 64, "deck_sha256": "b" * 64,
              "pad_driver_admission": "NOT_MEASURED",
              "input_transition_ns": 25.0, "library_max_transition_ns": 1.5,
              "output_load_pf": 5.0, "available_driver_max_capacitance_pf": 1.017}
    record.update(updates)
    return record


class TestClassifyEndpoint:
    def test_hold_violation(self):
        assert mod.classify_endpoint(50, 50, 1, is_hold=True) == "hold_violation"

    def test_skew_dominant(self):
        assert mod.classify_endpoint(50, 50, 10,
                                       skew_dominant=True) == "clock_skew_limited"

    def test_deep_logic(self):
        assert mod.classify_endpoint(30, 30, 50) == "logic_depth_limited"

    def test_net_dominant(self):
        assert mod.classify_endpoint(20, 70, 10) == "net_delay_limited"

    def test_cell_dominant(self):
        assert mod.classify_endpoint(70, 20, 10) == "cell_delay_limited"

    def test_default_picks_larger(self):
        # neither hits the 60% threshold, neither is deep — pick bigger
        assert mod.classify_endpoint(30, 40, 10) == "net_delay_limited"
        assert mod.classify_endpoint(40, 30, 10) == "cell_delay_limited"


class TestMakeFinding:
    def test_attaches_fix_strategy(self):
        f = mod.make_finding("rip_reg/D", -0.5, cell_delay_pct=80)
        assert f.category == "cell_delay_limited"
        assert "upsize" in f.fix_strategy.lower()

    def test_negative_slack_short_depth_is_hold(self):
        f = mod.make_finding("d/Q", -0.1, logic_depth=1, cell_delay_pct=30,
                              net_delay_pct=30)
        assert f.category == "hold_violation"

    def test_conflicting_input_boundary_precedes_pipeline_recommendation(self):
        f = mod.make_finding("tl_i[38] -> synthetic_register", -2.645125,
                             cell_delay_pct=100.0, net_delay_pct=0.0,
                             logic_depth=50, boundary_role="input",
                             boundary=boundary())
        assert f.category == "constraint_boundary_limited"
        assert f.boundary_state == "CONFLICT"
        assert f.boundary_source_sha256 == "a" * 64
        assert f.boundary_deck_sha256 == "b" * 64
        assert "RTL" in f.fix_strategy and "pipeline" not in f.fix_strategy

    def test_legal_boundary_preserves_genuine_deep_logic_classification(self):
        f = mod.make_finding("in -> deep_reg", -1.0, logic_depth=50,
                             boundary_role="input",
                             boundary=boundary(pad_driver_admission="ADMITTED"))
        assert f.boundary_state == "PASS"
        assert f.category == "logic_depth_limited"

    def test_missing_source_binding_refuses_repair_classification(self):
        f = mod.make_finding("in -> reg", -1.0, logic_depth=50,
                             boundary_role="input",
                             boundary=boundary(source_sha256=""))
        assert f.category == "constraint_boundary_unresolved"
        assert f.boundary_state == "UNRESOLVED"

    def test_conflicting_output_load_is_boundary_limited(self):
        f = mod.make_finding("reg -> out", -1.0, cell_delay_pct=80,
                             boundary_role="output", boundary=boundary())
        assert f.category == "constraint_boundary_limited"

    def test_absent_boundary_keeps_legacy_classification(self):
        f = mod.make_finding("in -> deep_reg", -1.0, logic_depth=50)
        assert f.boundary_state is None
        assert f.category == "logic_depth_limited"


class TestParseStaSummary:
    def test_basic_summary(self):
        text = "report_wns\nwns -1.25\nreport_tns\ntns -42.0\n"
        wns, tns = mod.parse_sta_summary(text)
        assert wns == -1.25
        assert tns == -42.0

    def test_no_match_returns_none(self):
        wns, tns = mod.parse_sta_summary("nothing here")
        assert wns is None and tns is None


class TestBuildReport:
    def test_counts(self):
        ep = [mod.make_finding("a", -1, cell_delay_pct=80),
              mod.make_finding("b", -1, net_delay_pct=80),
              mod.make_finding("c", -1, net_delay_pct=80)]
        rep = mod.build_report(ep, wns=-1.0, tns=-3.0)
        assert rep.counts_by_category["net_delay_limited"] == 2
        assert rep.counts_by_category["cell_delay_limited"] == 1
        assert rep.total_violations == 3


class TestMarkdownEmit:
    def test_includes_wns(self):
        ep = [mod.make_finding("a", -1, cell_delay_pct=80)]
        rep = mod.build_report(ep, wns=-1.0, tns=-1.0)
        md = mod.report_to_markdown(rep)
        assert "WNS: -1.0" in md

    def test_attribution(self):
        rep = mod.build_report([], wns=0, tns=0)
        md = mod.report_to_markdown(rep)
        assert "sta_triage_classify.py" in md
        assert f"(v{shipped_plugin_version()})." in md


def test_cli_accepts_boundary_record_and_emits_non_rtl_state(tmp_path, monkeypatch):
    endpoints = tmp_path / "endpoints.json"
    evidence = tmp_path / "boundary.json"
    output = tmp_path / "out.json"
    endpoints.write_text(json.dumps([{
        "endpoint": "tl_i[38] -> synthetic_register", "slack_ns": -2.645125,
        "cell_delay_pct": 100.0, "net_delay_pct": 0.0, "logic_depth": 50,
        "boundary_role": "input"}]))
    evidence.write_text(json.dumps(boundary()))
    monkeypatch.setattr(sys, "argv", ["sta_triage_classify.py",
        "--endpoints-json", str(endpoints), "--boundary-json", str(evidence),
        "--wns", "-2.645125", "--tns", "-2.645125", "--out-json", str(output)])
    assert mod._cli() == 0
    result = json.loads(output.read_text())
    assert result["findings"][0]["category"] == "constraint_boundary_limited"
    assert result["counts_by_category"]["constraint_boundary_limited"] == 1
