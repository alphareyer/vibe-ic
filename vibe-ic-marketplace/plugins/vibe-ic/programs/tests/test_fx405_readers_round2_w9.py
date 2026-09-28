"""Wave-9 controls for Phase-1 oracle refusal and canonical admission."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _input_ingest as ingest  # noqa: E402
import _progress_run as progress  # noqa: E402
import canonical_run_admission as admission  # noqa: E402
import l3_opcode_dispatch_key_actionable_check as dispatch_gate  # noqa: E402
import l3_opcode_name_coverage_check as name_gate  # noqa: E402
import phase1_doc_one_shot_runner as phase1  # noqa: E402
import test_fx405_readers_round2 as round2  # noqa: E402


def _write(project: Path, rel: str, text: str) -> Path:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _skip_log(project: Path) -> dict:
    return json.loads((project / "phase1/extraction_skipped.json").read_text())


def test_oracle_refusal_is_excluded_and_cannot_make_l3_unread(tmp_path):
    project = tmp_path / "design"
    _write(project, "input/docs/L1_product.md", "# Control unit\n")
    _write(project, "input/docs/L2_arch.md", "# Architecture\n")
    _write(project, "input/docs/golden/L3_alt.md", "# GOLDOP oracle\n")
    phase1.extract_text_pipeline(project)
    log = _skip_log(project)
    assert log["skipped"] == []
    assert [row["path"] for row in log["excluded_oracle"]] == [
        "input/docs/golden/L3_alt.md"]
    assert ingest.unread_input_documents(project) == []
    _write(project, "phase1/generated_docs/L3_CMD_PROTOCOL.json",
           json.dumps({"opcodes": []}))
    assert dispatch_gate.evaluate(project)["verdict"] == "VACUOUS_PASS"
    assert name_gate.main([str(project)]) == 0

    # A real converter-empty document remains UNREAD; the default is not
    # weakened to make oracle refusals look clean.
    _write(project, "input/docs/blank.md", "")
    phase1.extract_text_pipeline(project, force=True)
    assert [row["path"] for row in ingest.unread_input_documents(project)] == [
        "input/docs/blank.md"]


def test_single_doc_readme_fallback_cannot_ingest_oracle_tree(tmp_path):
    project = tmp_path / "design"
    _write(project, "input/docs/request.md",
           "# Small controller\nA controller with a status register.\n")
    _write(project, "input/golden/README.md", "GOLDTOKEN top is oracle_top\n")
    _write(project, "input/expected/README.md", "EXPTOKEN clock 900 MHz\n")
    extracted = phase1.extract_text_pipeline(project)
    assert not any("golden/README" in key or "expected/README" in key
                   for key in extracted)
    log = _skip_log(project)
    assert {row["path"] for row in log["excluded_oracle"]} >= {
        "input/golden/README.md", "input/expected/README.md"}
    assert ingest.unread_input_documents(project) == []

    run = progress.run([sys.executable, str(PROGRAMS / "phase1_doc_one_shot_runner.py"),
                        str(project)],
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                       poll_s=10)
    assert run.returncode == 0, (run.stdout or "")[-1500:] + (run.stderr or "")[-1500:]
    l1 = json.loads((project / "phase1/generated_docs/L1_DATASHEET.json").read_text())
    l9 = json.loads((project / "phase1/generated_docs/L9_INTEGRATION_SPEC.json").read_text())
    assert "EXPTOKEN" not in json.dumps(l1)
    assert "GOLDTOKEN" not in json.dumps(l1)
    assert "EXPTOKEN" not in json.dumps(l9)
    assert "GOLDTOKEN" not in json.dumps(l9)
    assert not any("GOLDTOKEN" in p.read_text(errors="replace") or
                   "EXPTOKEN" in p.read_text(errors="replace")
                   for p in (project / "phase1/input_doc").glob("*.txt"))


def test_denied_document_removes_its_stale_extraction_cache(tmp_path):
    project = tmp_path / "design"
    _write(project, "input/docs/request.md", "# Controller\n")
    _write(project, "input/docs/golden/L3_alt.md", "# GOLDOP\n")
    stale = _write(project, "phase1/input_doc/golden__L3_alt.txt", "GOLDOP\n")
    phase1.extract_text_pipeline(project)
    assert not stale.exists()
    assert "GOLDOP" not in phase1._v1_6_collect_input_docs_text(project)


def test_oracle_addition_does_not_re_admit_an_unchanged_design(tmp_path):
    project = tmp_path / "design"
    _write(project, "input/rtl/top.v", "module top; endmodule\n")
    oracle = _write(project, "input/golden/top.v", "module gold; endmodule\n")
    kwargs = {"container_image": "declared-image", "config": {},
              "program_paths": ()}
    first = admission.admit(project, "phase2", **kwargs)
    assert first.admitted
    oracle.write_text("module gold_changed; endmodule\n")
    edit = admission.build_identity(project, "phase2", **kwargs)
    assert admission.identity_sha256(edit) == first.identity_sha256
    _write(project, "input/golden/extra.v", "module extra; endmodule\n")
    added = admission.build_identity(project, "phase2", **kwargs)
    assert admission.identity_sha256(added) == first.identity_sha256
    duplicate = admission.admit(project, "phase2", **kwargs)
    assert duplicate.admitted is False
    assert duplicate.reason == "DUPLICATE_NO_NEW_EVIDENCE"
    assert set(added["source_input_excluded_oracle"]) == {
        "input/golden/top.v", "input/golden/extra.v"}


def test_unexercised_readers_are_not_counted_as_driven():
    static_only = {
        "_autoemit_chip_top_wrapper", "step_dft_lec_chain",
        "_design_top_input_ports", "step_prelayout_signoff",
        "_emit_declared_process_sta",
    }
    assert not (static_only & set(round2.READERS))


def test_staged_pdk_header_reader_excludes_oracle_named_liberty(tmp_path):
    project = tmp_path / "design"
    design = _write(project, "input/pdk/liberty/typ.lib",
                    "library(design_cells) {}\n")
    oracle = _write(project, "input/pdk/liberty/typ_ref.lib",
                    "library(oracle_cells) {}\n")
    staged = phase1._staged_pdk_enablement_files(project)
    assert str(oracle.relative_to(project)) not in staged
    assert str(design.relative_to(project)) in staged
    declared = phase1._declared_library_names(
        project, [str(oracle.relative_to(project)),
                  str(design.relative_to(project))])
    assert declared == [("input/pdk/liberty/typ.lib", "design_cells")]


@pytest.mark.parametrize("fixture", ["sha1", "sha256", "aes", "litescope"])
def test_checked_in_design_readme_is_invariant_to_oracle_sibling(
        tmp_path, fixture):
    from _hostpaths import require_repo
    source = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic",
                          "programs", "tests", "phase1_fixtures", fixture,
                          "README.md")
    design_text = source.read_text(encoding="utf-8")
    assert design_text.strip()
    clean = tmp_path / "clean"
    staged = tmp_path / "staged"
    for project in (clean, staged):
        _write(project, "input/docs/request.md", design_text)
    _write(staged, "input/golden/README.md", "# ORACLE_SENTINEL\n")
    clean_docs = phase1.extract_text_pipeline(clean)
    staged_docs = phase1.extract_text_pipeline(staged)
    assert clean_docs["request.md"].strip()
    assert staged_docs == clean_docs
    assert ingest.unread_input_documents(staged) == []
    assert "input/golden/README.md" in {
        row["path"] for row in _skip_log(staged)["excluded_oracle"]}
