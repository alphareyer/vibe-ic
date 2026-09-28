"""A PDK synthesis recipe is elected from matched post-route measurements."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as R  # noqa: E402


def _pair(design: str, pdk: str = "famxD") -> list[dict]:
    common = dict(pdk=pdk, design=design,
                  source_sha256=hashlib.sha256(design.encode()).hexdigest(),
                  liberty_sha256="b" * 64, constraints_sha256="c" * 64,
                  image_digest="d" * 64, fanout_cap=3,
                  drc="PASS", lvs="PASS",
                  max_fanout_violations=0)
    return [dict(common, recipe="fanout_buffer", area_um2=200,
                 postroute_slack_ns=1.0),
            dict(common, recipe="abc_alt_buffer", area_um2=190,
                 postroute_slack_ns=1.2)]


def _select(pdk: str, rows: list[dict]) -> dict:
    # The fallback lets this test make a behavioural assertion on main: the
    # missing selector produces NOT_MEASURED, while matched evidence elects it.
    select = getattr(R, "_select_postroute_synth_recipe",
                     lambda *_a, **_k: {"verdict": "NOT_MEASURED"})
    return select(pdk, rows, fanout_cap=3)


def test_two_neutral_designs_elect_a_measured_recipe():
    rows = _pair("logic_a") + _pair("logic_b")
    got = _select("famxD", rows)
    assert got["verdict"] == "PASS" and got["recipe"] == "abc_alt_buffer", got


def test_second_pdk_and_unmatched_inputs_cannot_borrow_a_win():
    rows = _pair("logic_a") + _pair("logic_b")
    assert _select("famyD", rows)["verdict"] == "NOT_MEASURED"
    rows[-1]["source_sha256"] = "e" * 64
    assert _select("famxD", rows)["verdict"] == "NOT_MEASURED"


def test_second_design_regression_blocks_the_recipe():
    rows = _pair("logic_a") + _pair("logic_b")
    rows[-1]["postroute_slack_ns"] = 0.9
    assert _select("famxD", rows)["verdict"] == "NOT_MEASURED"


def test_buffer_choice_is_measured_and_cap_scoped():
    rows = _pair("logic_a") + _pair("logic_b")
    for row in rows:
        if row["recipe"] == "abc_alt_buffer":
            row["recipe"] = "abc_no_buffer"
    got = _select("famxD", rows)
    assert got["verdict"] == "PASS" and got["recipe"] == "abc_no_buffer"
    rows[-1]["fanout_cap"] = 9
    assert _select("famxD", rows)["verdict"] == "NOT_MEASURED"


def test_unimplemented_full_adder_mapping_cannot_be_elected():
    rows = _pair("logic_a") + _pair("logic_b")
    for row in rows:
        if row["recipe"] == "abc_alt_buffer":
            row["recipe"] = "fa_map_alt_buffer"
    assert _select("famxD", rows)["verdict"] == "NOT_MEASURED"


def test_postroute_metrics_and_signoff_are_required():
    rows = _pair("logic_a") + _pair("logic_b")
    rows[-1].pop("postroute_slack_ns")
    assert _select("famxD", rows)["verdict"] == "NOT_MEASURED"
    rows = _pair("logic_a") + _pair("logic_b")
    rows[-1]["drc"] = "FAIL"
    assert _select("famxD", rows)["verdict"] == "NOT_MEASURED"


def test_reverse_mutation_removing_the_winning_record():
    rows = _pair("logic_a") + _pair("logic_b")
    assert _select("famxD", rows)["verdict"] == "PASS"
    assert _select("famxD", rows[:-1])["verdict"] == "NOT_MEASURED"


@pytest.mark.parametrize("variant", ["abc_alt_buffer", "abc_no_buffer"])
def test_step_synth_emits_only_the_elected_abc_variant(tmp_path, monkeypatch,
                                                       variant):
    project = tmp_path / "run"
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "core.v").write_text(
        "module core(input a, output y); assign y=a; endmodule\n")
    lib = tmp_path / "test.lib"
    lib.write_text("library(test) { cell(INV) { area : 1.0; } }\n")
    pdk = R.PdkConfig(name="famxD", liberty=str(lib), tech_lef="test.lef",
                      cell_lef="test.lef", cell_gds=None, site="unit",
                      drc_deck=None)
    monkeypatch.setattr(R, "_to_container_path", lambda path, container: path)
    monkeypatch.setattr(R, "_synth_max_fanout",
                        lambda *_a, **_k: (3, "declared", []))
    if hasattr(R, "_srp"):
        rows = _pair("logic_a") + _pair("logic_b")
        for row in rows:
            if row["recipe"] == "abc_alt_buffer":
                row["recipe"] = variant
        monkeypatch.setattr(R._srp, "load",
                            lambda _path: (rows, ""))
    commands = []

    def fake_tool(_container, command, **_kwargs):
        commands.append(command)
        return 1, "", "synthetic tool failure"

    monkeypatch.setattr(R, "_docker_exec", fake_tool)
    result = R.step_synth(project, "core", pdk, "fake")
    assert result.status == "FAIL"
    synth = next(c for c in commands if "abc -liberty" in c)
    if variant == "abc_alt_buffer":
        assert "&dch;&nf,{D};&put;buffer,-N,3" in synth, synth
    else:
        assert "buffer,-N,3" not in synth, synth
