"""R-0929-DIEID-BASIS: die ID is operator-specific (the shuttle operator's
generate_id). On a DIE deliverable with no operator it stays NOT_APPLICABLE with
the basis "no operator die-ID requirement for deliverable DIE" -- not "no package".

RED on main, measured on the spm IC/DIE run (cx_spmic2_run, 8HD-4, 2026-09-28):
reports/phase3/die_finishing.json recorded die_id.packaging_basis =
"phase1/generated_docs/L1_DATASHEET.json declares no_package_in_input=true ...",
i.e. the package basis, although the declaration on disk is an owner self-tape-out
DIE with no operator. The fixture is that run's declaration inputs, copied.

A declared operator (an ingested slot, route SHUTTLE) keeps the requirement owed:
the half stays NOT_DETERMINED until packaging is declared, even when L1 says no
package.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import die_finishing_gen as G

FIX = Path(__file__).parent / "fixtures" / "r0929_dieid_basis_self_tapeout_die"
BASIS = "no operator die-ID requirement for deliverable DIE"


def _copy(tmp_path) -> Path:
    p = tmp_path / "run"
    shutil.copytree(FIX, p)
    return p


def test_measured_self_tapeout_die_records_the_no_operator_basis(tmp_path):
    p = _copy(tmp_path)
    got = G.die_id_state({}, None, project=p)
    assert got["state"] == "NOT_APPLICABLE"
    assert got["packaging_basis"] == BASIS
    assert "package" not in got["packaging_basis"]
    assert BASIS in got["reason"]
    assert "does not gate the seal ring" in got["reason"]


def test_the_basis_constant_is_the_ruling_verbatim():
    assert G.DIE_ID_NO_OPERATOR_BASIS == BASIS


def test_a_declared_operator_keeps_die_id_owed(tmp_path):
    """NEGATIVE ARM: the same run with an operator slot selected (the owner's
    answers name a template slot) is route SHUTTLE; the die-ID requirement is
    owed, so the half is NOT_DETERMINED -- never NOT_APPLICABLE, not even with
    L1's no-package declaration beside it."""
    p = _copy(tmp_path)
    ans = p / "input/step_0_5ic_answers.json"
    doc = json.loads(ans.read_text())
    doc["operator_template"] = {"path": "input/submission_template",
                                "slot": "1x1", "absent_reason": None}
    ans.write_text(json.dumps(doc))
    assert G.operator_route(p) == "SHUTTLE"
    got = G.die_id_state({}, None, project=p)
    assert got["state"] == "NOT_DETERMINED"
    assert "OWED" in got["reason"]
    assert got.get("packaging_basis") != BASIS


def test_operator_declared_cob_packaging_still_judges_the_cells(tmp_path):
    """A declared chip-on-board packaging is still judged on the cells; the
    no-operator basis is consulted only where no packaging was declared."""
    p = _copy(tmp_path)
    got = G.die_id_state({"die_id": {"packaging": "cob", "cells": ["a"]}},
                         {"id_cells": {}}, project=p)
    assert got["state"] == "ABSENT"


def test_no_declaration_falls_back_to_the_prior_paths(tmp_path):
    p = _copy(tmp_path)
    shutil.rmtree(p / "input")
    assert G.operator_route(p) is None
    # L1's positive no-package declaration is still read where no route exists
    got = G.die_id_state({}, None, project=p)
    assert got["state"] == "NOT_APPLICABLE"
    assert got["packaging_basis"] != BASIS
