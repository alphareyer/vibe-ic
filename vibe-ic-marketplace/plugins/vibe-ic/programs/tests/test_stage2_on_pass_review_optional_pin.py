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
import os
import shutil
import subprocess
import sys
from pathlib import Path

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
