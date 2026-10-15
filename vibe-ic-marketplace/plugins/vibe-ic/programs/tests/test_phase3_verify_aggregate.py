"""Unit tests for `phase3_verify_aggregate.py`."""
import importlib

from _shipped_version import shipped_plugin_version  # noqa: E402  (#800)
mod = importlib.import_module("phase3_verify_aggregate")


class TestParseDrcCount:
    def test_parses_total_violations(self, tmp_path):
        rpt = tmp_path / "drc.rpt"
        rpt.write_text("Total violations: 42\n")
        assert mod.parse_drc_count(rpt) == 42

    def test_parses_drc_errors_found(self, tmp_path):
        rpt = tmp_path / "drc.rpt"
        rpt.write_text("Number of DRC errors found: 7\n")
        assert mod.parse_drc_count(rpt) == 7

    def test_missing_file_returns_minus_one(self, tmp_path):
        assert mod.parse_drc_count(tmp_path / "missing.rpt") == -1


class TestParseStaMargins:
    def test_parses_wns_tns(self, tmp_path):
        rpt = tmp_path / "sta.rpt"
        rpt.write_text("wns -1.5\ntns -7.0\n")
        wns, tns = mod.parse_sta_margins(rpt)
        assert wns == -1.5
        assert tns == -7.0

    def test_missing_returns_zero(self, tmp_path):
        wns, tns = mod.parse_sta_margins(tmp_path / "missing")
        assert wns == 0.0 and tns == 0.0


class TestAggregator:
    def _c(self, ec):
        return mod.CheckResult(name="x", exit_code=ec, stdout_tail="")

    def _present_all(self):
        return [mod.ArtifactPresence(name=n, path=f"/x/{r}", present=True)
                for n, r in mod.REQUIRED_FILES]

    def test_clean_silicon_passes(self):
        rep = mod.aggregate(mod.Path("/x"), self._present_all(),
                             [self._c(0)], drc_violations=0,
                             wns=0.5, tns=0.0)
        assert rep.verdict == "PASS"

    def test_drc_violations_fails(self):
        rep = mod.aggregate(mod.Path("/x"), self._present_all(),
                             [self._c(0)], drc_violations=10,
                             wns=0.5, tns=0.0)
        assert rep.verdict == "FAIL"

    def test_negative_wns_fails(self):
        rep = mod.aggregate(mod.Path("/x"), self._present_all(),
                             [self._c(0)], drc_violations=0,
                             wns=-0.1, tns=0.0)
        assert rep.verdict == "FAIL"

    def test_missing_artifact_fails(self):
        arts = [mod.ArtifactPresence(name="final_gds", path="/x/g",
                                       present=False)]
        rep = mod.aggregate(mod.Path("/x"), arts, [self._c(0)],
                             drc_violations=0, wns=0.5, tns=0.0)
        assert rep.verdict == "FAIL"

    def test_unknown_drc_count_is_UNMEASURED_not_PASS(self):
        """This test used to assert `verdict == "PASS"` for drc=-1, and that
        assertion WAS the defect vibe-ic#727 names.

        drc=-1 means the count could not be determined — `parse_drc_count`
        carries no XML dialect, so a KLayout report database (the format every
        sign-off certificate in this corpus uses) is unparseable to it by
        construction. "Not a failure" was right; "therefore a PASS" was not.
        They are different facts, and a reader of a green verdict could not tell
        which one it meant.

        Still not a FAIL — the property this test was written for is preserved.
        """
        rep = mod.aggregate(mod.Path("/x"), self._present_all(),
                             [self._c(0)], drc_violations=-1,
                             wns=0.5, tns=0.0)
        assert rep.verdict != "FAIL", "an unreadable count is not a violation"
        assert rep.verdict == "UNMEASURED", (
            "nor is it a clean run — those must not share a verdict")

    def test_attribution(self):
        rep = mod.aggregate(mod.Path("/x"), [], [],
                             drc_violations=0, wns=0, tns=0)
        d = rep.as_dict()
        assert d["emitted_by"] == \
            f"phase3_verify_aggregate v{shipped_plugin_version()}"


def test_backing_checks_are_shipped_canonical_consumers(tmp_path):
    expected = {"phase23_completion_self_audit_check.py", "drc_report_check.py",
                "lvs_report_check.py", "sta_report_check.py"}
    assert set(mod.BACKING_CHECKS) == expected
    for name in mod.BACKING_CHECKS:
        assert (mod.PROGRAMS_DIR / name).is_file()
        child = mod._run_check(name, tmp_path)
        assert child.argv[1] == str(mod.PROGRAMS_DIR / name)
        assert child.exit_code != 127
        assert "can't open file" not in child.stderr_tail
    assert "--signoff" in mod._run_check("drc_report_check.py", tmp_path).argv
    sta = mod._run_check("sta_report_check.py", tmp_path)
    assert sta.argv[-2:] == ["--under", "phase3/stage3/sta/post_route_timing.rpt"]


def test_missing_checker_retains_stderr_and_is_not_measured(tmp_path):
    child = mod._run_check("unshipped_public_checker.py", tmp_path)
    assert not child.passed
    assert child.status == "NOT_MEASURED"
    assert "can't open file" in child.stderr_tail
    assert child.argv[1].endswith("unshipped_public_checker.py")


def test_canonical_artifacts_are_discovered_without_legacy_aliases(tmp_path):
    paths = ["phase2/stage2/synth/public_synth.v", "phase3/stage3/pnr/public.gds",
             "phase3/stage3/pnr/public.def", "phase3/stage3/sta/post_route_timing.rpt",
             "reports/phase3/drc_signoff.rpt", "reports/phase3/lvs.rpt"]
    for rel in paths:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public source fixture\n")
    rows = mod.scan_artifacts(tmp_path)
    assert all(row.present for row in rows), rows
    assert {str(mod.Path(row.path).relative_to(tmp_path)) for row in rows} == set(paths)


def test_missing_sta_is_not_displayed_as_measured_zero(tmp_path):
    rep = mod.verify(tmp_path)
    assert rep.verdict == "FAIL"
    assert rep.sta_wns_ns is None and rep.sta_tns_ns is None
    assert all(check.exit_code != 127 for check in rep.checks)
