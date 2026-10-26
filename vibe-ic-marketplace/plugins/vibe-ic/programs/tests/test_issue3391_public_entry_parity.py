"""The public structural CLI can take the ordinary consumer's exact contract."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import formal_structural_check as fsc  # noqa: E402


CLAIMS = [
    {"rule": "clock_edge", "signal": "clk", "value": "posedge"},
    {"rule": "reset_port", "signal": "reset_n", "value": ""},
    {"rule": "reset_polarity", "signal": "reset_n", "value": "active_low"},
    {"rule": "reset_sync", "signal": "reset_n", "value": "synchronous"},
]


def _contract(rtl: Path) -> dict:
    return {
        "structural_subject": {
            "top": "neutral", "clock": "clk", "simdef": "-DSIMULATION ",
            "resets": ["reset_n"], "rtl_files": [str(rtl)],
        },
        "unresolved_obligations": [
            {"id": f"row-{i}", "program_rule": {"kind": "STRUCTURAL", "claims": [claim]}}
            for i, claim in enumerate(CLAIMS)
        ],
    }


def _write(tmp_path: Path, body: dict) -> tuple[Path, Path]:
    rtl = tmp_path / "neutral.v"
    rtl.write_text("module neutral(input clk,reset_n); endmodule\n")
    contract = tmp_path / "property_contract.json"
    contract.write_text(json.dumps(body))
    return rtl, contract


def _refuse_via_cli(tmp_path: Path, monkeypatch, capsys, body, stem: str) -> dict:
    rtl = tmp_path / "neutral.v"
    rtl.write_text("module neutral(input clk,reset_n); endmodule\n")
    contract = tmp_path / f"{stem}.json"
    contract.write_text(body if isinstance(body, str) else json.dumps(body))
    output = tmp_path / f"{stem}-result.json"
    monkeypatch.setattr(fsc, "check", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("check must not run")))
    assert fsc.main(["--rtl", str(rtl), "--top", "neutral",
                     "--property-contract", str(contract), "--json", str(output)]) == 2
    capsys.readouterr()
    record = json.loads(output.read_text())
    assert record["verdict"] == "NOT_MEASURED" and record["claims"] == []
    return record


def test_actual_cli_forwards_the_ordinary_contract_byte_for_byte(tmp_path, monkeypatch, capsys):
    rtl = tmp_path / "neutral.v"
    rtl.write_text("module neutral(input clk,reset_n); endmodule\n")
    contract = tmp_path / "property_contract.json"
    contract.write_text(json.dumps(_contract(rtl)))
    observed = {}

    def intercept(rtl_files, top, claims, work_dir, container=None, simdef="", klass=None):
        observed.update(rtl_files=rtl_files, top=top, claims=claims, work_dir=work_dir,
                        container=container, simdef=simdef, klass=klass)
        return {"program": "formal_structural_check", "version": fsc.VERSION,
                "verdict": fsc.PASS, "claims": claims}

    monkeypatch.setattr(fsc, "check", intercept)
    output = tmp_path / "result.json"
    rc = fsc.main(["--rtl", str(rtl), "--top", "neutral",
                   "--property-contract", str(contract),
                   "--work-dir", str(tmp_path / "work"), "--json", str(output)])
    capsys.readouterr()
    record = json.loads(output.read_text())
    assert rc == 0
    assert observed == {
        "rtl_files": [rtl], "top": "neutral", "claims": CLAIMS,
        "work_dir": tmp_path / "work", "container": None,
        "simdef": "-DSIMULATION ",
        "klass": {"clock": "clk", "resets": ["reset_n"]},
    }
    assert record["public_entry_contract"]["claims"] == CLAIMS
    assert (record["public_entry_contract"]["sha256"] ==
            record["public_entry_contract"]["sha256_after"])


def test_cli_without_property_contract_keeps_the_existing_defaults(tmp_path, monkeypatch, capsys):
    rtl = tmp_path / "neutral.v"
    rtl.write_text("module neutral; endmodule\n")
    observed = {}

    def intercept(*args, **kwargs):
        observed.update(args=args, kwargs=kwargs)
        return {"verdict": fsc.PASS, "claims": []}

    monkeypatch.setattr(fsc, "check", intercept)
    assert fsc.main(["--rtl", str(rtl), "--top", "neutral"]) == 0
    capsys.readouterr()
    assert observed["kwargs"] == {"simdef": "", "klass": None}


def test_malformed_contract_refuses_without_calling_check(tmp_path, monkeypatch, capsys):
    record = _refuse_via_cli(tmp_path, monkeypatch, capsys, "{", "malformed")
    assert "valid JSON" in record["error"]


def test_missing_or_wrong_types_refuse(tmp_path, monkeypatch, capsys):
    rtl = tmp_path / "neutral.v"
    rtl.write_text("module neutral; endmodule\n")
    bodies = [
        {},
        {"structural_subject": {"top": "neutral", "clock": "clk", "simdef": "",
                                "resets": "reset_n", "rtl_files": [str(rtl)]},
         "unresolved_obligations": []},
    ]
    for i, body in enumerate(bodies):
        _refuse_via_cli(tmp_path, monkeypatch, capsys, body, f"wrong-type-{i}")


def test_unsafe_define_and_unbound_clock_or_reset_refuse(tmp_path, monkeypatch, capsys):
    rtl = tmp_path / "neutral.v"
    rtl.write_text("module neutral; endmodule\n")
    mutations = []
    unsafe = _contract(rtl)
    unsafe["structural_subject"]["simdef"] = "-DSIMULATION; shell"
    mutations.append((unsafe, "approved chip-read define"))
    wrong_clock = _contract(rtl)
    wrong_clock["structural_subject"]["clock"] = "other_clk"
    mutations.append((wrong_clock, "clock_edge claim"))
    wrong_reset = _contract(rtl)
    wrong_reset["structural_subject"]["resets"] = ["other_reset"]
    mutations.append((wrong_reset, "reset_port claim"))
    for i, (body, expected) in enumerate(mutations):
        record = _refuse_via_cli(tmp_path, monkeypatch, capsys, body, f"mutation-{i}")
        assert expected in record["error"]


def test_repeated_cli_subject_or_claim_cannot_override_contract(tmp_path, monkeypatch, capsys):
    rtl, contract = _write(tmp_path, _contract(tmp_path / "neutral.v"))
    monkeypatch.setattr(fsc, "check", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("check must not run")))
    cases = [
        ["--rtl", str(rtl), "--top", "other"],
        ["--rtl", str(rtl), "--top", "neutral", "--claim", "reset_port:other:"],
    ]
    for i, prefix in enumerate(cases):
        output = tmp_path / f"refusal-{i}.json"
        assert fsc.main(prefix + ["--property-contract", str(contract),
                                  "--json", str(output)]) == 2
        capsys.readouterr()
        assert json.loads(output.read_text())["verdict"] == "NOT_MEASURED"
