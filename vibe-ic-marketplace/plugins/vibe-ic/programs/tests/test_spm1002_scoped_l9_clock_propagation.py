"""SPM-1002: a matching PDK-scoped L8 clock supersedes stale L9 prose."""

import json

import _path_layout
import phase1_doc_one_shot_runner as runner


def _write_docs(project, l8, l9, target="gf180mcuD"):
    docs = _path_layout.generated_docs_dir(project)
    docs.mkdir(parents=True)
    (docs / "L8_RTL_CONSTANTS.json").write_text(json.dumps(l8))
    l9_path = docs / "L9_INTEGRATION_SPEC.json"
    l9_path.write_text(json.dumps(l9))
    (docs / "L19_CONSTRAINTS_PDK.json").write_text(json.dumps({
        "fields": {"pdk_target": target},
    }))
    return l9_path


def test_matching_pdk_scoped_l8_replaces_stale_l9_prose_clock(tmp_path):
    project = tmp_path / "project"
    l9_path = _write_docs(
        project,
        {"clock_domains": [{
            "name": "clk", "period_ns": 24.0,
            "freq_mhz": 1000.0 / 24.0,
            "freq_hz": 1000000000.0 / 24.0,
            "role": "primary", "domain_kind": "primary",
            "pdk_scoped_target": "gf180mcuD",
            "extraction_strategy": "clock_domain_pdk_scoped_row",
            "evidence": {"file": "input/docs/L1_product_metadata.md",
                         "line": 37},
        }]},
        {"clock_domains": [{
            "name": "clk", "period_ns": 10.0,
            "freq_mhz": 100.0, "freq_hz": 100000000.0,
            "role": "primary", "domain_kind": "primary",
            "extraction_strategy": "clock_domain_doc_prose_fmax",
            "evidence": {"file": "input/docs/L1_product_metadata.md",
                         "line": 35},
        }], "clocks": [{
            "name": "clk", "period_ns": 10.0,
            "freq_mhz": 100.0, "freq_hz": 100000000.0,
            "role": "primary",
            "extraction_strategy": "l9_top_port_clock_derive",
        }]},
    )

    conflicts = runner._post_emit_mirror_clock_resets_to_l9_v1_6_311(project)

    assert conflicts == []
    l9 = json.loads(l9_path.read_text())
    row = l9["clock_domains"][0]
    assert row["period_ns"] == 24.0
    assert row["freq_hz"] == 1000000000.0 / 24.0
    assert row["pdk_scoped_target"] == "gf180mcuD"
    assert any(m["period_ns"] == 10.0
               and m["role"] == "displaced_by_pdk_scoped_row"
               for m in row["alternate_frequency_mentions"])
    assert l9["clocks"][0]["period_ns"] == 24.0
    assert l9["clocks"][0]["pdk_scoped_target"] == "gf180mcuD"


def test_unscoped_l8_and_l9_owners_still_refuse(tmp_path):
    project = tmp_path / "project"
    l9_path = _write_docs(
        project,
        {"clock_domains": [{
            "name": "clk", "period_ns": 24.0, "role": "primary",
        }]},
        {"clock_domains": [{
            "name": "clk", "period_ns": 10.0, "role": "primary",
        }]},
    )

    conflicts = runner._post_emit_mirror_clock_resets_to_l9_v1_6_311(project)

    assert len(conflicts) == 1
    l9 = json.loads(l9_path.read_text())
    assert l9["clock_domains"][0]["period_ns"] == 10.0
    assert l9["clock_contract_conflicts"][0]["resolution"] == "refused"


def test_same_scoped_l9_owner_conflict_uses_mixed_case_run_target(tmp_path):
    project = tmp_path / "project"
    l9_path = _write_docs(
        project,
        {"clock_domains": [{
            "name": "clk", "period_ns": 24.0, "role": "primary",
            "pdk_scoped_target": "gf180mcuD",
        }]},
        {"clock_domains": [{
            "name": "clk", "period_ns": 10.0, "role": "primary",
            "pdk_scoped_target": "gf180mcuD",
        }]},
    )

    conflicts = runner._post_emit_mirror_clock_resets_to_l9_v1_6_311(project)

    assert len(conflicts) == 1
    l9 = json.loads(l9_path.read_text())
    assert l9["clock_domains"][0]["period_ns"] == 10.0
    assert l9["clock_contract_conflicts"][0]["resolution"] == "refused"
