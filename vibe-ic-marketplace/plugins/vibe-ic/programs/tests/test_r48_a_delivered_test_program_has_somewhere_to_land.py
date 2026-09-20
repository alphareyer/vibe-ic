"""The flow looks in the design INPUT for the case's own delivered test program.

MEASURED on subservient x gf180mcuD as a DIE, FRONT DOOR, run r48 (lane
icsub2, host 8HD-4, tree 41d3b39b8, image 0.3.67 sha256:4e9f54ef), from the
pristine dataset:

    reports/phase2/sim/l10_oracle_provenance.json
      counts: {"SUBSTANCE_FLOOR": 9, "GENERATED": 1}

One of ten declared L10 cases executed its own oracle, and that one
(`reset_n_cycle_instruction`) was GENERATED in-flow by
`_emit_case_boot_latency_oracle`. The same IC scored 10/10 on earlier runs
only because nine testbenches had been placed into `phase2/stage1/sim/tb/`
BY HAND, where `authored_oracle_preserved` found them
(counts there: {"PRESERVED_AUTHORED": 9, "GENERATED": 1}).

The reason a front-door run could not reproduce that: NO step in this producer
ever read the design INPUT for a delivered test program. `_resolve_from_design_input`
resolves a MODULE DEFINITION a testbench instantiates — not a testbench and not
a program. So a dataset could deliver the case's own program and nothing in the
flow would consult it. These tests pin the path that makes delivery meaningful.

Both directions: with nothing delivered the emitted artefacts are what they
were, and a delivered file under a `golden`/`oracle`/`harness` segment is
refused rather than read (§4.05).
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import testbench_gen as T  # noqa: E402


def _case(name="blinky_hex", stimulus="blinky.hex"):
    return {"name": name, "kind": "functional_vector",
            "stimulus": stimulus, "expected": "GPIO toggles"}


def test_nothing_delivered_changes_nothing(tmp_path):
    (tmp_path / "input").mkdir()
    assert T.delivered_case_oracle(tmp_path, "blinky_hex") is None
    out = tmp_path / "tb"; out.mkdir()
    assert T._emit_case_delivered_oracle(tmp_path, _case(), out, {}) is None
    assert list(out.iterdir()) == []


def test_a_delivered_testbench_is_installed_into_the_l10_run(tmp_path):
    d = tmp_path / "input" / "sim" / "tb"; d.mkdir(parents=True)
    (d / "blinky_hex.v").write_text("module tb; initial $finish; endmodule\n")
    out = tmp_path / "tb"; out.mkdir()
    rep = {}
    got = T._emit_case_delivered_oracle(tmp_path, _case(), out, rep)
    assert got == out / "blinky_hex.v"
    text = got.read_text()
    assert "module tb" in text
    assert "// DELIVERED : input/sim/tb/blinky_hex.v" in text
    assert rep["delivered_oracles"][0]["from"] == "input/sim/tb/blinky_hex.v"


def test_the_stimulus_image_the_case_names_comes_with_it(tmp_path):
    d = tmp_path / "input" / "sim" / "tb"; d.mkdir(parents=True)
    (d / "blinky_hex.v").write_text("module tb; endmodule\n")
    pg = tmp_path / "input" / "sim" / "programs"; pg.mkdir(parents=True)
    (pg / "blinky.hex").write_text("@0000 13\n")
    out = tmp_path / "tb"; out.mkdir()
    rep = {}
    T._emit_case_delivered_oracle(tmp_path, _case(), out, rep)
    assert (out / "blinky.hex").read_text() == "@0000 13\n"
    assert rep["delivered_oracles"][0]["program"] == \
        "input/sim/programs/blinky.hex"


def test_a_program_the_case_did_not_name_is_not_fetched(tmp_path):
    pg = tmp_path / "input" / "sim" / "programs"; pg.mkdir(parents=True)
    (pg / "somethingelse.hex").write_text("x\n")
    assert T.delivered_case_program(tmp_path, "blinky.hex") is None


def test_an_oracle_tree_is_refused_even_under_input(tmp_path):
    """§4.05 — the design's own test programs, never a golden/oracle/harness."""
    for seg in ("golden", "oracle", "harness", "reference_flow"):
        d = tmp_path / "input" / "sim" / seg / "tb"; d.mkdir(parents=True)
        (d / "blinky_hex.v").write_text("module tb; endmodule\n")
    assert T.delivered_case_oracle(tmp_path, "blinky_hex") is None


def test_work_already_authored_in_the_run_is_not_clobbered(tmp_path):
    d = tmp_path / "input" / "sim" / "tb"; d.mkdir(parents=True)
    (d / "blinky_hex.v").write_text("module delivered; endmodule\n")
    out = tmp_path / "tb"; out.mkdir()
    (out / "blinky_hex.v").write_text("module authored_in_progress; endmodule\n")
    got = T._emit_case_delivered_oracle(tmp_path, _case(), out, {})
    assert got == out / "blinky_hex.v"
    assert "authored_in_progress" in got.read_text()


def test_an_empty_delivered_file_is_not_an_oracle(tmp_path):
    d = tmp_path / "input" / "sim" / "tb"; d.mkdir(parents=True)
    (d / "blinky_hex.v").write_text("   \n")
    out = tmp_path / "tb"; out.mkdir()
    assert T._emit_case_delivered_oracle(tmp_path, _case(), out, {}) is None


def test_the_run_says_the_oracle_was_delivered_not_hand_placed(tmp_path):
    """The distinction r46-vs-r48 turned on has to be readable afterwards."""
    d = tmp_path / "input" / "sim" / "tb"; d.mkdir(parents=True)
    (d / "blinky_hex.v").write_text("module tb; endmodule\n")
    out = T._pl.sim_dir(tmp_path) / "tb"
    out.mkdir(parents=True)
    T._emit_case_delivered_oracle(tmp_path, _case(), out, {})
    prov = T.oracle_provenance(tmp_path)
    row = [c for c in prov["cases"] if c["case"] == "blinky_hex"][0]
    assert row["source"] == T.ORACLE_SOURCE_DELIVERED
    assert row["delivered_from"] == "input/sim/tb/blinky_hex.v"
    assert prov["counts"].get(T.ORACLE_SOURCE_DELIVERED) == 1


def test_a_hand_placed_oracle_still_reads_as_hand_placed(tmp_path):
    """The control: no DELIVERED stamp, no delivered claim."""
    out = T._pl.sim_dir(tmp_path) / "tb"
    out.mkdir(parents=True)
    (out / "blinky_hex.v").write_text("module tb; endmodule\n")
    prov = T.oracle_provenance(tmp_path)
    row = [c for c in prov["cases"] if c["case"] == "blinky_hex"][0]
    assert row["source"] == T.ORACLE_SOURCE_AUTHORED
