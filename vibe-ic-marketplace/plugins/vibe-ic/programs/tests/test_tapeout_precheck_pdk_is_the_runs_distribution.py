"""With no `--pdk`, step 37.5ic grades the die against the distribution the RUN built.

MEASURED on spm x gf180mcuD, DIE route (v1.22.3): the runner invoked
`tapeout_precheck --pdk gf180mcuD` and the density rung passed (per-layer
densities 0.353-0.378 against the registry's windows). `flow_compliance_check`
then re-ran the step's yaml clause, which carries no `--pdk`, and overwrote the
report: `resolve_pdk` answered the declared FAMILY `gf180mcu`, the registry has
no such entry, `Checker.KLayoutDensity` came back FAIL (`unknown-pdk`) and
`General.ForbiddenLayers` NOT_DETERMINED -- in the report the run shipped.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import tapeout_precheck as T  # noqa: E402


def _record(project: Path, pdk="gf180mcuD", source="--pdk"):
    p = project / T.RUN_PDK_RECORD_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pdk": pdk, "pdk_source": source,
                             "pdk_family_resolved": "gf180mcu"}))


def _declared(monkeypatch, value):
    monkeypatch.setattr(T, "_resolve_declared_pdk",
                        lambda project, explicit="": (
                            (explicit, "--pdk") if explicit.strip()
                            else (value, "decl" if value else None)))


def test_the_family_is_refined_to_the_distribution_the_run_built(tmp_path, monkeypatch):
    _declared(monkeypatch, "gf180mcu")
    _record(tmp_path)
    pdk, src = T.resolve_pdk(tmp_path)
    assert pdk == "gf180mcuD"
    assert T.RUN_PDK_RECORD_REL in src and "'gf180mcu'" in src


def test_without_the_run_record_the_declared_answer_is_unchanged(tmp_path, monkeypatch):
    _declared(monkeypatch, "gf180mcu")
    assert T.resolve_pdk(tmp_path) == ("gf180mcu", "decl")
    _record(tmp_path, source="input/docs")          # a declaration, not --pdk
    assert T.resolve_pdk(tmp_path) == ("gf180mcu", "decl")
    (tmp_path / T.RUN_PDK_RECORD_REL).write_text("{not json")
    assert T.resolve_pdk(tmp_path) == ("gf180mcu", "decl")


def test_a_run_pdk_of_another_process_is_never_substituted(tmp_path, monkeypatch):
    _declared(monkeypatch, "sky130")
    _record(tmp_path, pdk="gf180mcuD")
    assert T.resolve_pdk(tmp_path) == ("sky130", "decl")


def test_no_declaration_takes_the_run_record_and_explicit_still_wins(tmp_path, monkeypatch):
    _declared(monkeypatch, None)
    _record(tmp_path)
    assert T.resolve_pdk(tmp_path)[0] == "gf180mcuD"
    assert T.resolve_pdk(tmp_path, "ihp-sg13g2") == ("ihp-sg13g2", "--pdk")


def test_the_registry_knows_the_distribution_and_not_the_family():
    import pdk_metal_density_windows as W
    assert W.windows_for_pdk("gf180mcuD")[1]["status"] == "stated"
    assert W.windows_for_pdk("gf180mcu")[1]["status"] == "unknown-pdk"
