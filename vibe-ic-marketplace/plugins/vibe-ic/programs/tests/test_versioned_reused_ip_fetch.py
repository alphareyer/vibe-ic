"""Step 1 fetches only a named, pinned component and records its bytes."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

from _hostpaths import require_repo
from ip_catalog_query import CatalogMatch, query_catalog


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _upstream(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "reusable"
    (repo / "rtl").mkdir(parents=True)
    (repo / "rtl/leaf.v").write_text("module leaf; wire fixed = 1'b0; endmodule\n")
    (repo / "soc_top.v").write_text("module soc_top; endmodule\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "tagged core")
    _git(repo, "tag", "2.3.4")
    (repo / "rtl/leaf.v").write_text("module leaf; wire fixed = 1'b1; endmodule\n")
    _git(repo, "add", "rtl/leaf.v")
    _git(repo, "commit", "-qm", "upstream erratum")
    return repo, _git(repo, "rev-parse", "HEAD")


def _match(repo: Path, erratum: str, *, self_match: bool = False) -> CatalogMatch:
    fields = dict(
        ip_name="leaf", category="cpu", version="2.3.4", license="MIT",
        canonical_url=str(repo), canonical_commit="2.3.4",
        matched_pattern="L2.cpu_isa", confidence=0.9, manifest_path="synthetic",
        rtl_files=["rtl/leaf.v"], self_match=self_match,
    )
    if "errata" in CatalogMatch.__dataclass_fields__:
        fields["errata"] = [{"upstream_commit": erratum, "file": "rtl/leaf.v",
                             "disclosure": "upstream correction"}]
    return CatalogMatch(**fields)


def _project(tmp_path: Path, text: str) -> Path:
    project = tmp_path / "fabric"
    docs = project / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L1_DATASHEET.json").write_text(json.dumps({"part_name": "fabric"}))
    (docs / "L2_FRS.json").write_text(json.dumps({
        "cpu_isa": "rv32i", "cpu_arch": "bit-serial", "notes": text}))
    return project


def test_step1_fetches_exact_upstream_core_and_discloses_erratum(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import design_one_shot_runner as runner
    import ip_catalog_query as query
    import ip_catalog_pull as pull

    repo, erratum = _upstream(tmp_path)
    match = _match(repo, erratum)
    monkeypatch.setattr(query, "query_catalog", lambda *a, **k: [match])
    monkeypatch.setattr(pull, "CACHE_ROOT", tmp_path / "cache")
    project = _project(tmp_path, "Reuse leaf from vendor:reusable:leaf:2.3.4.")

    result = runner.step_rtl_gen(project, "processor_cpu")
    assert result.status == "PASS_WITH_WAIVERS"
    assert "Fetched declared versioned reused IP ['leaf']" in result.detail
    assert result.extras["ip_fetch"]["n_ips_pulled"] == 1
    rtl = project / "phase2/stage1/rtl"
    assert (rtl / "leaf.v").read_text() == "module leaf; wire fixed = 1'b1; endmodule\n"
    assert not (rtl / "soc_top.v").exists()
    manifest = json.loads((rtl / "SOURCE_MANIFEST.json").read_text())
    pin = manifest["source_pins"][0]
    assert pin["version"] == "2.3.4"
    assert pin["checked_out_sha"] == _git(repo, "rev-parse", "2.3.4")
    assert pin["files_sha256"]["rtl/leaf.v"] == hashlib.sha256(
        (rtl / "leaf.v").read_bytes()).hexdigest()
    assert pin["errata_applied"][0]["upstream_commit"] == erratum
    provenance = [json.loads(line) for line in (project / "provenance.jsonl").read_text().splitlines()]
    pulls = [row for row in provenance if row.get("event") == "ip_catalog_pull"]
    assert len(pulls) == 1
    assert pulls[0]["errata_applied"][0]["upstream_commit"] == erratum
    assert pulls[0]["outputs"]["phase2/stage1/rtl/leaf.v"] == (
        "sha256:" + pin["files_sha256"]["rtl/leaf.v"])

    again = runner.step_rtl_gen(project, "processor_cpu")
    assert again.extras["ip_fetch"]["status"] == "ALREADY_FETCHED"
    assert len([row for row in (project / "provenance.jsonl").read_text().splitlines()
                if json.loads(row).get("event") == "ip_catalog_pull"]) == 1


@pytest.mark.parametrize("text,reason", [
    ("A generic bit-serial CPU. vendor:reusable:core:2.3.4.",
     "IP_REUSE_NAME_UNDECLARED: leaf"),
    ("Reuse leaf from the reusable repository.",
     "IP_REUSE_SOURCE_OR_VERSION_UNDECLARED: leaf"),
    ("Do not reuse leaf from vendor:reusable:leaf:2.3.4.",
     "IP_REUSE_DECLARATION_DENIED: leaf"),
])
def test_unnamed_or_unversioned_match_is_refused_by_name(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        text: str, reason: str) -> None:
    import design_one_shot_runner as runner
    import ip_catalog_query as query
    repo, erratum = _upstream(tmp_path)
    monkeypatch.setattr(query, "query_catalog", lambda *a, **k: [_match(repo, erratum)])
    project = _project(tmp_path, text)
    result = runner.step_rtl_gen(project, "processor_cpu")
    assert result.status == "PASS_WITH_WAIVERS"
    assert result.extras["ip_fetch_refusals"] == [reason]
    assert not (project / "phase2/stage1/rtl/leaf.v").exists()


def test_design_under_test_is_still_refused(tmp_path: Path) -> None:
    from ip_catalog_pull import pull_catalog_ip
    repo, erratum = _upstream(tmp_path)
    match = _match(repo, erratum, self_match=True)
    match.self_match_reason = "catalog entry supplies the IC-under-test's OWN design"
    project = _project(tmp_path, "Reuse leaf from vendor:reusable:leaf:2.3.4.")
    audit = pull_catalog_ip(match, project)
    assert audit["status"] == "REJECTED"
    assert audit["reason"] == match.self_match_reason
    assert not (project / "phase2/stage1/rtl/leaf.v").exists()


def test_real_catalog_component_has_versioned_source_and_scoped_erratum(
        tmp_path: Path) -> None:
    """Read the checked-in catalog artefact through its real query consumer."""
    manifest = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic",
                            "ip-catalog", "cpu", "serv", "manifest.yaml")
    project = _project(tmp_path, "Reuse servile CPU from award-winning:serv:servile:1.4.0.")
    docs = project / "input/docs"
    docs.mkdir(parents=True)
    (docs / "L8_integration.md").write_text(
        "sources: reference_serv/doc/interface.rst\n"
        "Reuse the SERV core via award-winning:serv:servile:1.4.0.\n")
    matches = query_catalog(project, catalog_dir=manifest.parent.parent.parent,
                            ic_name="fabric")
    assert [(m.ip_name, m.self_match, m.version) for m in matches] == [
        ("serv", False, "1.4.0")]
    assert {e["file"] for e in matches[0].errata} == {
        "servile/servile_rf_mem_if.v", "rtl/serv_state.v"}
    assert all(e["file"] in matches[0].rtl_files for e in matches[0].errata)
