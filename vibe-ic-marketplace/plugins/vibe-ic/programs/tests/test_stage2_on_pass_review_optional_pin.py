#!/usr/bin/env python3
"""Stage-2 R2 and a pin the intent itself declares OPTIONAL.

MEASURED on the subservient hardmacro run r32 (gf180mcuD, tree eecc69bec, lane
icsubaudit2): the design input's interface table carries a row
`(optional) i_gpio | optional | input | ... (if the Plugin adopts a
bidirectional GPIO)`, phase 1 wrote it into
`phase1/generated_docs/L9_INTEGRATION_SPEC.json::top_ports` with
`optional: true`, the design's own declaration chose one output-only GPIO, and
R2 REJECTED the run for not building `i_gpio`. The rejection put
`stage_on_pass_review` into `reports/audit/phase23_completion_audit.json`
`verdict_causes.failed_gates` of a PASS_WITH_WAIVERS run and was reported as a
design shortfall the input never asked for. `l9_rtl_pin_consistency_check`
(#491 R4) already reads the same field and treats that absence as advisory.

Every case here is the published `accept_spm` fixture with ONE pin row added
to its intent, so the only variable is the pin's own declaration.

  DISARM   an absent pin the intent marks `optional: true`      -> rc 0
  REJECT   the SAME pin with the flag removed                   -> rc 1
  REJECT   the flag given as a string, not the boolean True     -> rc 1
  REJECT   a required absent pin beside an optional absent one  -> rc 1,
           naming only the required pin; the EMITTED test agrees and goes
           green when only the required pin is built.
"""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
PROG = PROGRAMS / "stage_on_pass_review.py"
FLOW = PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"
ACCEPT = (Path(__file__).resolve().parent / "fixtures" / "stage2_on_pass_review"
          / "accept_spm")
L9_REL = Path("phase1") / "generated_docs" / "L9_INTEGRATION_SPEC.json"
NETLIST_REL = Path("phase2") / "stage2" / "synth" / "netlist.v"


def run(project, *extra):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    argv = [sys.executable, str(PROG), str(project), "--stage", "stage2",
            "--flow-def", str(FLOW), "--stage-verdict", "PASS"]
    return subprocess.run(argv + list(extra), capture_output=True, text=True,
                          env=env)


def cell(tmp_path, name, *pins):
    """accept_spm with `pins` appended to every intent pin field it carries."""
    d = tmp_path / name
    shutil.copytree(ACCEPT, d)
    l9p = d / L9_REL
    doc = json.loads(l9p.read_text(encoding="utf-8"))
    touched = 0
    for field in ("top_ports", "ports", "top_module_pins"):
        if isinstance(doc.get(field), list) and doc[field]:
            doc[field].extend(dict(p) for p in pins)
            touched += 1
    assert touched, "the fixture carries no intent pin field to extend"
    l9p.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return d


def gpio(**kw):
    pin = {"name": "i_gpio", "mode": "input", "direction": "input", "io": None,
           "evidence": "input/docs/L3_external_interface.md", "width": 1}
    pin.update(kw)
    return pin


def test_the_accept_fixture_itself_still_accepts(tmp_path):
    """The anchor: without it every case below could pass on a fixture that
    no longer accepts for an unrelated reason."""
    d = tmp_path / "plain"
    shutil.copytree(ACCEPT, d)
    r = run(d)
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_absent_pin_the_intent_declares_optional_disarms(tmp_path):
    r = run(cell(tmp_path, "opt", gpio(optional=True)),
            "--json", str(tmp_path / "r.json"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DISARMED" in r.stdout
    rec = json.loads((tmp_path / "r.json").read_text())
    assert rec["rejections"] == []
    f = rec["observations"][0]
    assert f["verdict"] == "DISARMED"
    assert [x["name"] for x in f["disarmed"]] == ["i_gpio"]
    assert f["disarmed"][0]["intent_declares_optional"] == "optional=True"
    assert "intent_declares_supply" not in f["disarmed"][0]
    assert "optional" in f["observation"]


def test_the_same_pin_without_the_flag_rejects(tmp_path):
    """The other direction on the same cell: the disarm is caused by the
    intent's own flag and nothing else."""
    r = run(cell(tmp_path, "req", gpio()), "--json", str(tmp_path / "r.json"))
    assert r.returncode == 1, r.stdout + r.stderr
    f = json.loads((tmp_path / "r.json").read_text())["rejections"][0]
    assert [p["name"] for p in f["absent_signal_pins"]] == ["i_gpio"]
    assert f["disarmed"] == []


def test_a_truthy_string_is_not_a_declaration_of_optional(tmp_path):
    """"false" is truthy. A disarm that read truthiness would silence a pin
    whose intent says the opposite."""
    for i, v in enumerate(("false", "true", 1)):
        r = run(cell(tmp_path, f"str{i}", gpio(optional=v)))
        assert r.returncode == 1, (v, r.stdout + r.stderr)


def test_an_optional_pin_does_not_hide_a_required_one(tmp_path):
    """A required absent pin beside an optional absent pin still rejects, the
    rejection names only the required one, and the run's EMITTED regression
    agrees with the review: it fails today and passes once the required pin
    alone is built, with the optional pin still absent."""
    d = cell(tmp_path, "mixed", gpio(optional=True),
             gpio(name="o_irq", mode="output", direction="output"))
    r = run(d, "--json", str(tmp_path / "r.json"))
    assert r.returncode == 1, r.stdout + r.stderr
    f = json.loads((tmp_path / "r.json").read_text())["rejections"][0]
    assert [p["name"] for p in f["absent_signal_pins"]] == ["o_irq"]
    assert [x["name"] for x in f["disarmed"]] == ["i_gpio"]

    emitted = d / f["test"]
    assert emitted.is_file(), f["test"]
    before = subprocess.run([sys.executable, str(emitted)],
                            capture_output=True, text=True)
    assert before.returncode == 1, before.stdout + before.stderr
    assert "o_irq" in before.stdout and "i_gpio" not in before.stdout

    n = d / NETLIST_REL
    n.write_text(n.read_text(encoding="utf-8", errors="replace").replace(
        "module spm(clk, rst, x, y, p);",
        "module spm(clk, rst, x, y, p, o_irq);\n  output o_irq;"),
        encoding="utf-8")
    after = subprocess.run([sys.executable, str(emitted)],
                           capture_output=True, text=True)
    assert after.returncode == 0, after.stdout + after.stderr


def documentary_cell(tmp_path):
    """The same published known-good artefact, with the producer's existing
    documentary projection. Neither synthesis nor a new interface is built."""
    d = cell(tmp_path, "documentary", gpio(name="retired_example", optional=False))
    l9p = d / L9_REL
    doc = json.loads(l9p.read_text())
    for field in ("top_ports", "ports", "top_module_pins"):
        for pin in doc.get(field, []):
            pin["declared_by_staged_top"] = pin["name"] != "retired_example"
    doc["selected_interface_binding"] = {
        "top_module": doc["top_module"],
        "selected_source": str(NETLIST_REL),
        "selected_source_sha256": hashlib.sha256((d / NETLIST_REL).read_bytes()).hexdigest(),
        "document_examples_not_implemented": ["retired_example"],
        "illustrative_groups": [{"l9": ["retired_example"], "rtl": ["y"]}],
    }
    l9p.write_text(json.dumps(doc))
    return d


def _assert_current_r2_contract(project, expected, absent, record_property):
    """Observe the real API and its normal emitted callable on exact input.

    The original PR's stricter metadata policy remains in its immutable
    ancestor. These assertions exercise the current source/required contract;
    the emitter is seeded only when the current input has no rejection.
    """
    import runpy
    from test_stage2_on_pass_review import _sopr, _r2_issue3131_metadata_only

    declaration = {"intent": [str(L9_REL)], "artefact": [str(NETLIST_REL)]}
    inputs = {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in project.rglob("*") if p.is_file()
              and not str(p.relative_to(project)).startswith("reports/")}
    finding, api_dispatch = _r2_issue3131_metadata_only(
        lambda: _sopr.rule_intent_pin_not_in_netlist(project, declaration))
    seed = finding
    if finding["verdict"] != "REJECT":
        l9 = project / L9_REL
        original = l9.read_bytes()
        content = json.loads(original)
        content["top_ports"].append({"name": "r73_seed_irq", "direction": "output"})
        try:
            l9.write_text(json.dumps(content))
            seed = _sopr.rule_intent_pin_not_in_netlist(project, declaration)
        finally:
            l9.write_bytes(original)
        assert seed["verdict"] == "REJECT"
    seed = dict(seed, rule="R2_INTENT_PIN_NOT_IN_NETLIST")
    emitted = _sopr.emit_test(project / "reports/tests/test_r73_r2.py", seed, "stage2")

    def check():
        namespace = runpy.run_path(str(emitted))
        try:
            namespace["test_every_pin_the_intent_declares_is_built_by_the_synthesised_top"]()
        except AssertionError as exc:
            return "REJECT", str(exc)
        return "ACCEPT", ""

    (observed, message), emitted_dispatch = _r2_issue3131_metadata_only(check)
    actual_absent = [p["name"] for p in finding.get("absent_signal_pins", [])]
    record_property("api_verdict", finding["verdict"])
    record_property("absent_signal_pins", json.dumps(actual_absent))
    record_property("actual_generated_callable", observed)
    record_property("actual_generated_message", message)
    record_property("fixture_sha256", json.dumps(inputs, sort_keys=True))
    record_property("api_process_dispatch_attempts", json.dumps(api_dispatch))
    record_property("generated_process_dispatch_attempts", json.dumps(emitted_dispatch))
    assert inputs == {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in project.rglob("*") if p.is_file()
                      and not str(p.relative_to(project)).startswith("reports/")}
    assert finding["verdict"] == expected
    assert actual_absent == absent
    assert observed == ("REJECT" if expected == "REJECT" else "ACCEPT"), message
    assert api_dispatch == emitted_dispatch == []
    for name in absent:
        assert name in message
    return finding


def test_current_documentary_binding_disarms_without_building_the_example(tmp_path, record_property):
    d = documentary_cell(tmp_path)
    r = run(d, "--json", str(tmp_path / "r.json"))
    assert r.returncode == 0, r.stdout + r.stderr
    rec = json.loads((tmp_path / "r.json").read_text())
    assert rec["rejections"] == []
    f = rec["observations"][0]
    assert f["verdict"] == "DISARMED"
    assert f["disarmed"][0]["name"] == "retired_example"
    assert "intent_declares_documentary" in f["disarmed"][0]
    _assert_current_r2_contract(d, "DISARMED", [], record_property)


@pytest.mark.parametrize("change", [
    "binding_absent", "source_absent", "hash_absent", "hash_changed",
    "top_changed", "source_changed", "source_symlink", "retired_absent",
    "retired_string", "retired_duplicate", "retired_unknown", "flag_absent",
    "flag_true", "flag_string", "flag_zero", "required_true", "mandatory_true",
    "group_absent", "group_unknown_replacement", "group_duplicate",
    "alias_conflict", "duplicate_pin", "replacement_flag_absent",
    "documentary_pin_built", "l9_top_changed", "pin_name_absent", "retired_invalid_name",
])
def test_unproven_or_contradictory_documentary_release_still_rejects(tmp_path, change, record_property):
    """Retain original case IDs while explicitly migrating obsolete policy.

    Group/cardinality/replacement/marker-only/contained-symlink metadata does
    not add a current R2 obligation. A truly built pin accepts; a nameless row
    cannot earn named documentary credit. All genuine binding, literal flag,
    required/mandatory and source-port negatives still reject.
    """
    d = documentary_cell(tmp_path)
    l9p = d / L9_REL
    doc = json.loads(l9p.read_text())
    b = doc["selected_interface_binding"]
    rows = doc["top_ports"]
    pin = next(p for p in rows if p["name"] == "retired_example")
    if change == "binding_absent": del doc["selected_interface_binding"]
    elif change == "source_absent": del b["selected_source"]
    elif change == "hash_absent": del b["selected_source_sha256"]
    elif change == "hash_changed": b["selected_source_sha256"] = "0" * 64
    elif change == "top_changed": b["top_module"] = "foreign_top"
    elif change == "source_changed": b["selected_source"] = "foreign/netlist.v"
    elif change == "source_symlink":
        target = d / "native.v"
        (d / NETLIST_REL).rename(target)
        (d / NETLIST_REL).symlink_to(target)
    elif change == "retired_absent": del b["document_examples_not_implemented"]
    elif change == "retired_string": b["document_examples_not_implemented"] = "retired_example"
    elif change == "retired_duplicate": b["document_examples_not_implemented"] *= 2
    elif change == "retired_unknown": b["document_examples_not_implemented"] = ["unknown_example"]
    elif change == "flag_absent": del pin["declared_by_staged_top"]
    elif change == "flag_true": pin["declared_by_staged_top"] = True
    elif change == "flag_string": pin["declared_by_staged_top"] = "false"
    elif change == "flag_zero": pin["declared_by_staged_top"] = 0
    elif change == "required_true": pin["required"] = True
    elif change == "mandatory_true": pin["mandatory"] = True
    elif change == "group_absent": del b["illustrative_groups"]
    elif change == "group_unknown_replacement": b["illustrative_groups"][0]["rtl"] = ["unbuilt"]
    elif change == "group_duplicate": b["illustrative_groups"] *= 2
    elif change == "alias_conflict":
        doc["ports"] = [dict(p) for p in rows]
        next(p for p in doc["ports"] if p["name"] == "retired_example")["declared_by_staged_top"] = True
    elif change == "duplicate_pin": rows.append(dict(pin))
    elif change == "replacement_flag_absent":
        del next(p for p in rows if p["name"] == "y")["declared_by_staged_top"]
    elif change == "documentary_pin_built":
        for field in ("top_ports", "ports", "top_module_pins"):
            if field not in doc: continue
            doc[field] = [p for p in doc[field] if p["name"] != "retired_example"]
            next(p for p in doc[field] if p["name"] == "y")["declared_by_staged_top"] = False
        b["document_examples_not_implemented"] = ["y"]
        b["illustrative_groups"] = [{"l9": ["y"], "rtl": ["x"]}]
    elif change == "l9_top_changed": doc["top_module"] = "foreign_top"
    elif change == "pin_name_absent": del pin["name"]
    elif change == "retired_invalid_name": b["document_examples_not_implemented"] = [12]
    l9p.write_text(json.dumps(doc))
    metadata_release = {"source_symlink", "retired_duplicate", "group_absent",
                        "group_unknown_replacement", "group_duplicate",
                        "alias_conflict", "duplicate_pin", "replacement_flag_absent"}
    expected = ("DISARMED" if change in metadata_release else
                "ACCEPT" if change in {"documentary_pin_built", "pin_name_absent"}
                else "REJECT")
    absent = ["retired_example"] if expected == "REJECT" else []
    f = _assert_current_r2_contract(d, expected, absent, record_property)
    if change == "pin_name_absent":
        # No invented named-pin release for a row that has no name. The
        # remaining named obligations are all actually present in netlist.
        assert f.get("disarmed", []) == []
        assert set(p["name"] for p in rows if p.get("name")) == {"clk", "rst", "x", "y", "p"}
    if change == "documentary_pin_built":
        assert f.get("disarmed", []) == []
    r = run(d, "--json", str(tmp_path / "r.json"))
    assert r.returncode == (1 if expected == "REJECT" else 0), (change, r.stdout + r.stderr)
    rec = json.loads((tmp_path / "r.json").read_text())
    if expected == "REJECT":
        assert rec["rejections"][0]["rule"] == "R2_INTENT_PIN_NOT_IN_NETLIST"
        emitted = d / rec["rejections"][0]["test"]
        result = subprocess.run([sys.executable, str(emitted)], capture_output=True, text=True)
        assert result.returncode == 1, (change, result.stdout + result.stderr)
    else:
        assert rec["rejections"] == []


def test_documentary_disarm_keeps_required_pin_and_emitted_regression(tmp_path):
    d = documentary_cell(tmp_path)
    l9p = d / L9_REL
    doc = json.loads(l9p.read_text())
    doc["top_ports"].append({"name": "required_irq", "direction": "output",
                             "declared_by_staged_top": True})
    l9p.write_text(json.dumps(doc))
    r = run(d, "--json", str(tmp_path / "r.json"))
    assert r.returncode == 1, r.stdout + r.stderr
    f = json.loads((tmp_path / "r.json").read_text())["rejections"][0]
    assert [p["name"] for p in f["absent_signal_pins"]] == ["required_irq"]
    emitted = d / f["test"]
    before = subprocess.run([sys.executable, str(emitted)], capture_output=True, text=True)
    assert before.returncode == 1 and "required_irq" in before.stdout
    # Correct only the genuinely required declaration. The documentary pin
    # remains absent; the same standalone regression now accepts that fact.
    doc["top_ports"] = [p for p in doc["top_ports"] if p["name"] != "required_irq"]
    l9p.write_text(json.dumps(doc))
    after = subprocess.run([sys.executable, str(emitted)], capture_output=True, text=True)
    assert after.returncode == 0, after.stdout + after.stderr


@pytest.mark.parametrize("kind", ["optional", "supply"])
@pytest.mark.parametrize("documentary", [False, True])
def test_existing_legacy_disarm_keeps_an_exact_false_marker(tmp_path, kind, documentary):
    d = documentary_cell(tmp_path)
    l9p = d / L9_REL
    doc = json.loads(l9p.read_text())
    legacy = {"name": "legacy_absent", "direction": "input",
              "declared_by_staged_top": False}
    legacy.update({"optional": True} if kind == "optional" else {"role": "power"})
    for field in ("top_ports", "ports", "top_module_pins"):
        if field not in doc: continue
        if not documentary:
            doc[field] = [p for p in doc[field] if p["name"] != "retired_example"]
        doc[field].append(dict(legacy))
    if not documentary: del doc["selected_interface_binding"]
    l9p.write_text(json.dumps(doc))
    r = run(d, "--json", str(tmp_path / "r.json"))
    assert r.returncode == 0, r.stdout + r.stderr
    f = json.loads((tmp_path / "r.json").read_text())["observations"][0]
    assert f["verdict"] == "DISARMED"
    row = next(p for p in f["disarmed"] if p["name"] == "legacy_absent")
    assert "intent_declares_" + kind in row


@pytest.mark.parametrize("kind", ["optional", "supply"])
@pytest.mark.parametrize("contradiction", ["required", "alias"])
def test_legacy_false_does_not_hide_required_or_alias_contradictions(tmp_path, kind, contradiction, record_property):
    d = documentary_cell(tmp_path)
    l9p = d / L9_REL
    doc = json.loads(l9p.read_text())
    row = {"name": "legacy_absent", "direction": "input",
           "declared_by_staged_top": False}
    row.update({"optional": True} if kind == "optional" else {"role": "power"})
    doc["top_ports"].append(row)
    if contradiction == "required": row["required"] = True
    else:
        doc["ports"] = [dict(p) for p in doc["top_ports"]]
        next(p for p in doc["ports"] if p["name"] == "legacy_absent")["declared_by_staged_top"] = True
    l9p.write_text(json.dumps(doc))
    # Pure power belongs to physical acceptance; an alias marker by itself
    # creates no signal obligation. Literal required=True non-supply does.
    expected = "REJECT" if (kind, contradiction) == ("optional", "required") else "DISARMED"
    _assert_current_r2_contract(d, expected,
                                ["legacy_absent"] if expected == "REJECT" else [],
                                record_property)
    r = run(d, "--json", str(tmp_path / "r.json"))
    assert r.returncode == (1 if expected == "REJECT" else 0), r.stdout + r.stderr


@pytest.mark.parametrize("built", [False, True], ids=["missing", "built"])
def test_documentary_source_port_still_missing_from_netlist_rejects(tmp_path, built, record_property):
    """The migrated actual-built y positive keeps a real source-port control."""
    d = documentary_cell(tmp_path)
    l9p = d / L9_REL
    doc = json.loads(l9p.read_text())
    doc["top_ports"] = [p for p in doc["top_ports"] if p["name"] != "retired_example"]
    next(p for p in doc["top_ports"] if p["name"] == "y")["declared_by_staged_top"] = False
    # Explicit neutral supplement: source really declares y in both arms;
    # only the netlist header/declaration changes in the missing arm.
    source_rel = Path("phase2/stage1/rtl/selected_spm.v")
    source = d / source_rel
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes((d / NETLIST_REL).read_bytes())
    doc["selected_interface_binding"].update(
        selected_source=str(source_rel),
        selected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        document_examples_not_implemented=["y"],
        illustrative_groups=[{"l9": ["y"], "rtl": ["x"]}])
    l9p.write_text(json.dumps(doc))
    if not built:
        netlist = d / NETLIST_REL
        netlist.write_text(netlist.read_text().replace("x, y, p", "x, p")
                           .replace("  input y;\n", ""))
    finding = _assert_current_r2_contract(
        d, "ACCEPT" if built else "REJECT", [] if built else ["y"], record_property)
    assert finding.get("disarmed", []) == []


@pytest.mark.parametrize("kind", ["optional", "supply"])
@pytest.mark.parametrize("flag", ["required", "mandatory"])
@pytest.mark.parametrize("built", [False, True], ids=["missing", "built"])
def test_legacy_same_name_required_non_supply_alias(tmp_path, kind, flag, built, record_property):
    """A real required non-supply alias defeats optional/power shadowing."""
    d = documentary_cell(tmp_path)
    l9p = d / L9_REL
    doc = json.loads(l9p.read_text())
    row = {"name": "legacy_absent", "direction": "input", "declared_by_staged_top": False}
    row.update({"optional": True} if kind == "optional" else {"role": "power"})
    doc["top_ports"].append(row)
    doc["ports"] = [{"name": "legacy_absent", "direction": "input", flag: True}]
    l9p.write_text(json.dumps(doc))
    if built:
        n = d / NETLIST_REL
        n.write_text(n.read_text().replace(
            "module spm(clk, rst, x, y, p);",
            "module spm(clk, rst, x, y, p, legacy_absent);\n  input legacy_absent;"))
        # Keep the selected netlist binding current after the authored build.
        doc["selected_interface_binding"]["selected_source_sha256"] = hashlib.sha256(n.read_bytes()).hexdigest()
        l9p.write_text(json.dumps(doc))
    finding = _assert_current_r2_contract(
        d, "DISARMED" if built else "REJECT", [] if built else ["legacy_absent"], record_property)
    assert [p["name"] for p in finding["disarmed"]] == ["retired_example"]
