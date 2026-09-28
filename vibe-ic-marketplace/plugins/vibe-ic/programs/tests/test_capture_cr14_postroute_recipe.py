"""A PDK synthesis recipe is elected from matched post-route measurements."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as R  # noqa: E402
import synth_full_adder_map as FA  # noqa: E402


LIBERTY_FA = """library(neutral) {
  cell(FA_SMALL) {
    area: 2.0;
    pin(P) { direction: input; }
    pin(Q) { direction: input; }
    pin(R) { direction: input; }
    pin(CARRY) { direction: output; function: "(P&Q)|(P&R)|(Q&R)"; }
    pin(SUM) { direction: output; function: "P^Q^R"; }
  }
  cell(FA_LARGE) {
    area: 4.0;
    pin(P) { direction: input; }
    pin(Q) { direction: input; }
    pin(R) { direction: input; }
    pin(CARRY) { direction: output; function: "(P&Q)|(P&R)|(Q&R)"; }
    pin(SUM) { direction: output; function: "P^Q^R"; }
  }
}
"""


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


def _select(pdk: str, rows: list[dict], *, liberty_sha256: str = "b" * 64,
            image_digest: str = "d" * 64) -> dict:
    # The fallback lets this test make a behavioural assertion on main: the
    # missing selector produces NOT_MEASURED, while matched evidence elects it.
    select = getattr(R, "_select_postroute_synth_recipe",
                     lambda *_a, **_k: {"verdict": "NOT_MEASURED"})
    return select(pdk, rows, fanout_cap=3,
                  liberty_sha256=liberty_sha256, image_digest=image_digest)


def test_two_neutral_designs_elect_a_measured_recipe():
    rows = _pair("logic_a") + _pair("logic_b")
    got = _select("famxD", rows)
    assert got["verdict"] == "PASS" and got["recipe"] == "abc_alt_buffer", got


def test_second_pdk_and_unmatched_inputs_cannot_borrow_a_win():
    rows = _pair("logic_a") + _pair("logic_b")
    assert _select("famyD", rows)["verdict"] == "NOT_MEASURED"
    rows[-1]["source_sha256"] = "e" * 64
    assert _select("famxD", rows)["verdict"] == "NOT_MEASURED"


def test_history_without_current_liberty_and_image_cannot_elect_recipe():
    rows = _pair("logic_a") + _pair("logic_b")
    # The A/B rows agree with each other, but a caller that has not supplied
    # this synthesis invocation's two identities has no matching evidence.
    assert R._select_postroute_synth_recipe("famxD", rows, fanout_cap=3)[
        "verdict"] == "NOT_MEASURED"
    assert _select("famxD", rows, liberty_sha256="e" * 64)["verdict"] == "NOT_MEASURED"
    assert _select("famxD", rows, image_digest="f" * 64)["verdict"] == "NOT_MEASURED"
    assert _select("famxD", rows)["verdict"] == "PASS"


def test_step_synth_keeps_buffer_for_stale_environment(tmp_path, monkeypatch):
    project = tmp_path / "run"
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "core.v").write_text("module core(input a, output y); assign y=a; endmodule\n")
    lib = tmp_path / "current.lib"
    lib.write_text("library(neutral) { cell(INV) { area : 1.0; } }\n")
    pdk = R.PdkConfig(name="famxD", liberty=str(lib), tech_lef="test.lef",
                      cell_lef="test.lef", cell_gds=None, site="unit", drc_deck=None)
    rows = _pair("logic_a") + _pair("logic_b")
    for row in rows:
        if row["recipe"] == "abc_alt_buffer":
            row["recipe"] = "abc_no_buffer"
    monkeypatch.setattr(R._srp, "load", lambda _path: (rows, ""))
    monkeypatch.setattr(R, "_to_container_path", lambda path, _c: path)
    monkeypatch.setattr(R, "_synth_max_fanout", lambda *_a, **_k: (3, "declared", []))
    monkeypatch.setattr(R, "_current_synth_liberty_sha256",
                        lambda _path, _container: hashlib.sha256(lib.read_bytes()).hexdigest(),
                        raising=False)
    monkeypatch.setattr(R, "_step_image_digest", lambda _container: "d" * 64)
    commands = []
    monkeypatch.setattr(R, "_docker_exec", lambda _c, command, **_k:
                        (commands.append(command) or 1, "", "synthetic tool failure"))
    R.step_synth(project, "core", pdk, "fake")
    synth = next(c for c in commands if "abc -liberty" in c)
    assert "buffer,-N,3" in synth


def test_current_liberty_identity_reads_active_bytes_each_time(tmp_path, monkeypatch):
    lib = tmp_path / "active.lib"
    lib.write_bytes(b"first")
    monkeypatch.setattr(R._cex, "no_container_route", lambda: True)
    assert R._current_synth_liberty_sha256(str(lib), "") == hashlib.sha256(b"first").hexdigest()
    lib.write_bytes(b"second")
    assert R._current_synth_liberty_sha256(str(lib), "") == hashlib.sha256(b"second").hexdigest()

    calls = []
    monkeypatch.setattr(R._cex, "no_container_route", lambda: False)

    def measured_container_hash(container, command, **_kwargs):
        calls.append((container, command))
        class Result:
            returncode = 0
            stdout = hashlib.sha256(b"container bytes").hexdigest() + "  /mounted/active.lib\n"
        return Result()

    monkeypatch.setattr(R._cex, "run_in_container_supervised", measured_container_hash)
    assert R._current_synth_liberty_sha256("/mounted/active.lib", "active") == (
        hashlib.sha256(b"container bytes").hexdigest())
    assert calls == [("active", "sha256sum /mounted/active.lib")]


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


def test_full_adder_mapping_needs_proof_and_nonzero_cell_census():
    rows = _pair("logic_a") + _pair("logic_b")
    for row in rows:
        if row["recipe"] == "abc_alt_buffer":
            row["recipe"] = "fa_map_alt_buffer"
    assert _select("famxD", rows)["verdict"] == "NOT_MEASURED"
    for row in rows:
        if row["recipe"] == "fa_map_alt_buffer":
            row.update(lec="PASS", mapped_fa_count=2)
    got = _select("famxD", rows)
    assert got["verdict"] == "PASS" and got["recipe"] == "fa_map_alt_buffer"
    rows[-1]["mapped_fa_count"] = 0
    assert _select("famxD", rows)["verdict"] == "NOT_MEASURED"


def test_liberty_truth_table_and_area_discover_neutral_full_adder():
    spec = FA.discover(LIBERTY_FA)
    assert spec == ("FA_SMALL", ("P", "Q", "R"), "CARRY", "SUM")
    mapping = FA.verilog_map(spec)
    assert ".CARRY(X[i])" in mapping and ".SUM(Y[i])" in mapping
    assert FA.discover(LIBERTY_FA.replace('function: "P^Q^R"',
                                          'function: "P|Q|R"')) is None
    assert FA.discover(LIBERTY_FA.replace('function: "P^Q^R"',
                                          'function: "P?Q:R"')) is None
    assert FA.discover(LIBERTY_FA.replace("area: 4.0", "area: 2.0")) is None


def test_step_synth_stages_elected_full_adder_actuator(tmp_path, monkeypatch):
    project = tmp_path / "run"
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "core.v").write_text(
        "module core(input a,b,c, output s,k); "
        "assign s=a^b^c; assign k=(a&b)|(a&c)|(b&c); endmodule\n")
    lib = tmp_path / "neutral.lib"
    lib.write_text(LIBERTY_FA)
    pdk = R.PdkConfig(name="famxD", liberty=str(lib), tech_lef="test.lef",
                      cell_lef="test.lef", cell_gds=None, site="unit",
                      drc_deck=None)
    monkeypatch.setattr(R, "_to_container_path", lambda path, _c: path)
    monkeypatch.setattr(R, "_synth_max_fanout",
                        lambda *_a, **_k: (3, "declared", []))
    monkeypatch.setattr(R, "_current_synth_liberty_sha256",
                        lambda _path, _container: "b" * 64)
    monkeypatch.setattr(R, "_step_image_digest", lambda _container: "d" * 64)
    rows = _pair("logic_a") + _pair("logic_b")
    for row in rows:
        if row["recipe"] == "abc_alt_buffer":
            row.update(recipe="fa_map_alt_buffer", lec="PASS",
                       mapped_fa_count=1)
    monkeypatch.setattr(R._srp, "load", lambda _path: (rows, ""))
    commands = []
    monkeypatch.setattr(R, "_docker_exec",
                        lambda _c, command, **_k:
                        (commands.append(command) or 1, "", "synthetic failure"))
    result = R.step_synth(project, "core", pdk, "fake")
    assert result.status == "FAIL"
    command = next(c for c in commands if "extract_fa -fa" in c)
    assert "read_liberty -lib" in command
    assert "techmap -map" in command
    assert "&dch;&nf,{D};&put;buffer,-N,3" in command
    assert "FA_SMALL mapped" in (R._pl.synth_dir(project) /
                                 "_measured_fa_map.v").read_text()

    # A successful Yosys rc that reports extracted adders but drops the
    # mapped cell must not advance synthesis to PASS.
    def fake_lost_cell(_container, _command, **_kwargs):
        (R._pl.synth_dir(project) / "core_synth.v").write_text(
            "module core(input a,b,c, output s,k); "
            "assign s=a^b^c; assign k=a&b; endmodule\n")
        return 0, "Created $fa cell synthetic_extraction", ""

    monkeypatch.setattr(R, "_docker_exec", fake_lost_cell)
    lost = R.step_synth(project, "core", pdk, "fake")
    assert lost.status == "FAIL" and "FA_MAP_LOST" in lost.detail


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
    monkeypatch.setattr(R, "_current_synth_liberty_sha256",
                        lambda _path, _container: "b" * 64)
    monkeypatch.setattr(R, "_step_image_digest", lambda _container: "d" * 64)
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
