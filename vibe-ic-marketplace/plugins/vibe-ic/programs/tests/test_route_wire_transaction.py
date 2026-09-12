"""Executable contract tests for the in-session native-route transaction.

The transaction deliberately keeps the original OpenDB wire alive while a
native trial wire is routed.  This test checks the Tcl contract emitted to the
real OpenROAD consumer: reject must restore the original wire identity and a
byte-identical DEF *before* it republishes the pre-trial DRC report.
"""
from __future__ import annotations

import sys
from pathlib import Path


_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

from _route_wire_transaction import wire_transaction_tcl  # noqa: E402


def test_begin_makes_an_exclusive_pretrial_checkpoint_and_native_trial_wire():
    tcl = wire_transaction_tcl()
    assert "proc _vic_wire_begin {directory report}" in tcl
    assert 'error "WIRE_TRIAL_DIRECTORY_EXISTS $directory"' in tcl
    assert "write_def $directory/before.def" in tcl
    assert "file copy $report $directory/before.drc.rpt" in tcl
    assert "$wire detach" in tcl
    assert "set trial [odb::dbWire_create $net]" in tcl
    assert "$trial append $wire" in tcl


def test_accept_only_discards_the_saved_original_wire():
    tcl = wire_transaction_tcl()
    accept = tcl[tcl.index("if {$accept}"):tcl.index("# Preserve the rejected evidence")]
    assert "odb::dbWire_destroy $wire" in accept
    assert "return" in accept
    assert "restored.def" not in accept


def test_reject_preserves_evidence_then_restores_wire_identity_and_def_bytes():
    tcl = wire_transaction_tcl()
    reject = tcl[tcl.index("# Preserve the rejected evidence"):]
    assert "file copy $report $directory/rejected.drc.rpt" in reject
    assert "write_def $directory/rejected.def" in reject
    assert "$wire attach $net" in reject
    assert "WIRE_RESTORE_ID_MISMATCH" in reject
    assert "write_def $directory/restored.def" in reject
    assert "WIRE_RESTORE_DEF_MISMATCH" in reject
    # The selected report cannot be restored before the full DEF comparison.
    assert reject.index("WIRE_RESTORE_DEF_MISMATCH") < reject.index(
        "file copy -force $directory/before.drc.rpt $report")


def test_reject_removes_a_trial_wire_when_the_original_net_was_unwired():
    tcl = wire_transaction_tcl()
    assert "if {$trial ne \"NULL\"} { odb::dbWire_destroy $trial }" in tcl
