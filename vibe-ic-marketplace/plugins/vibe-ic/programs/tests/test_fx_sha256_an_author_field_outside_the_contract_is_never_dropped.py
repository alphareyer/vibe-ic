"""`spec_declaration_emit --set` for a field the spec's contract does not list.

MEASURED (sha256 x sky130A, front door, spec-to-rtl author pass): the author
declared `--set register_byte_order=big` -- the byte order the L10 register-bus
producer reads from `plugin_output/declaration.json` when the documents are
silent -- and the emitter printed `PASS — 7/7 contract field(s) declared`
while the key never reached the file. The author's only route was to edit the
declaration by hand.

A declared choice is either written, with its provenance, or refused by name.
It is never dropped while the program says PASS. Every test drives the real
program as a subprocess. No chip, field vocabulary or design literal is
assumed by the program; the field names here are generic.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
EMITTER = PROGRAMS / "spec_declaration_emit.py"
DECL = "plugin_output/declaration.json"

CONTRACT = """# L7

The Plugin MUST declare `{path}` before authoring:

| Field | Required | Example |
|---|---|---|
| `handshake_style` | Yes | `"valid_ready"` |
| `endianness_note` | optional | `"n/a"` |
""".format(path=DECL)


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / "input" / "docs").mkdir(parents=True)
    (p / "input" / "docs" / "L7_verification_plan.md").write_text(CONTRACT)
    return p


def _emit(p: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(EMITTER), str(p), *args],
                          capture_output=True, text=True, timeout=120)


def _decl(p: Path) -> dict:
    return json.loads((p / DECL).read_text())


def _sidecar(p: Path) -> dict:
    f = p / DECL
    return json.loads(f.with_name(f.stem + ".provenance.json").read_text())


def test_a_field_outside_the_contract_is_written_with_its_provenance(tmp_path):
    """RED on main: rc 0, 'PASS', and the key is not in the file."""
    p = _project(tmp_path)
    cp = _emit(p, "--set", "handshake_style=valid_ready",
               "--set", "register_word_order=high_first")
    assert cp.returncode == 0, cp.stderr
    assert _decl(p).get("register_word_order") == "high_first", _decl(p)
    rec = _sidecar(p).get("declared_outside_contract", {})
    assert rec.get("register_word_order", {}).get("provenance") \
        == "author_declared", rec
    assert "register_word_order" in cp.stdout      # said, not silent


def test_authored_outside_provenance_survives_a_plain_runner_rerun(tmp_path):
    p = _project(tmp_path)
    first = _emit(p, "--set", "handshake_style=valid_ready",
                  "--set", "register_word_order=high_first")
    assert first.returncode == 0, first.stderr
    original = _sidecar(p)["declared_outside_contract"]["register_word_order"]

    second = _emit(p)  # the normal runner invocation supplies no --set
    assert second.returncode == 0, second.stderr
    assert _decl(p)["register_word_order"] == "high_first"
    assert _sidecar(p)["declared_outside_contract"]["register_word_order"] == original
    assert "register_word_order" not in _sidecar(p)["preserved_foreign_keys"]


def test_optional_only_contract_gives_the_actual_no_write_reason(tmp_path):
    p = _project(tmp_path)
    doc = p / "input" / "docs" / "L7_verification_plan.md"
    doc.write_text(CONTRACT.replace("| `handshake_style` | Yes |", "| `handshake_style` | optional |"))
    cp = _emit(p, "--set", "register_word_order=high_first")
    assert cp.returncode == 4
    assert "NOT written: register_word_order" in cp.stderr
    assert "no contract field was determined" in cp.stderr
    assert "while a REQUIRED field is undetermined" not in cp.stderr


def test_a_placeholder_outside_the_contract_is_refused_by_name(tmp_path):
    """`TBD` states no choice; it is named and not written."""
    p = _project(tmp_path)
    cp = _emit(p, "--set", "handshake_style=valid_ready",
               "--set", "register_word_order=TBD")
    assert "register_word_order" not in _decl(p)
    assert "register_word_order" in (cp.stdout + cp.stderr)


def test_a_refusal_names_the_outside_field_it_did_not_write(tmp_path):
    """Fail-closed writes nothing; it must say the outside field went nowhere."""
    p = _project(tmp_path)
    cp = _emit(p, "--set", "register_word_order=high_first")
    assert cp.returncode == 1
    assert not (p / DECL).exists()
    assert "register_word_order" in cp.stderr


def test_null_retracts_an_outside_field_already_declared(tmp_path):
    p = _project(tmp_path)
    _emit(p, "--set", "handshake_style=valid_ready",
          "--set", "register_word_order=high_first")
    cp = _emit(p, "--set", "register_word_order=null")
    assert cp.returncode == 0, cp.stderr
    assert "register_word_order" not in _decl(p)
    assert _decl(p)["handshake_style"] == "valid_ready"


def test_contract_fields_are_unchanged_by_the_outside_route(tmp_path):
    """Control, GREEN on main: the contract field is written exactly as before."""
    p = _project(tmp_path)
    cp = _emit(p, "--set", "handshake_style=valid_ready")
    assert cp.returncode == 0, cp.stderr
    assert _decl(p) == {"handshake_style": "valid_ready"}


@pytest.mark.parametrize("key,value", [
    ("ip_catalog_used", '[{"name":"sha256_core"}]'),
    ("ai_authored_files", '["rtl/top.v"]'),
    ("supplied_rtl", '{"files":[]}'),
])
def test_producer_owned_outside_keys_are_refused_not_rewritten(tmp_path, key, value):
    p = _project(tmp_path)
    before = {"handshake_style": "valid_ready", key: {"flow": True}}
    path = p / DECL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(before))
    cp = _emit(p, "--set", f"{key}={value}")
    assert cp.returncode == 0
    assert _decl(p)[key] == before[key]
    assert key in (cp.stdout + cp.stderr) and "REFUSED" in (cp.stdout + cp.stderr)
