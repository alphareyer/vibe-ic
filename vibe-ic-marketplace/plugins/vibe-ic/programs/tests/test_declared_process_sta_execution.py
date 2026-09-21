import subprocess
from pathlib import Path
from test_declared_process_sta_producer import scene, p3


def test_stale_report_cannot_supply_missing_execution(scene,monkeypatch):
    s=scene;assert s['emit']()
    monkeypatch.setattr(p3,'_docker_exec',lambda *a,**k:(0,'',''))
    assert not s['emit']()


def test_error_diagnostic_with_zero_rc_refused(scene,monkeypatch):
    original=p3._docker_exec
    def run(*a,**k):
        original(*a,**k);return 0,'Error: link failed',''
    monkeypatch.setattr(p3,'_docker_exec',run)
    assert not scene['emit']()


def test_tcl_execution_with_neutral_command_stubs(scene,monkeypatch):
    s=scene
    prelude='''
proc read_liberty {args} {}
proc read_verilog {args} {}
proc link_design {args} {}
proc read_sdc {args} {}
proc read_spef {args} {}
proc all_clocks {} {return clk}
proc set_propagated_clock {args} {}
proc set_timing_derate {args} {}
proc get_cells {args} {return {neutral_cell}}
proc neutral_cell {method} {
 if {$method eq "is_leaf"} {return 1}
 if {$method eq "pin_iterator"} {return neutral_iterator}
 return neutral_master
}
proc neutral_iterator {method} {
 if {$method eq "has_next"} {return 0}
 return ""
}
proc get_full_name {obj} {return $obj}
proc report_parasitic_annotation {args} {
 set f [open [lindex $args end] a]
 puts $f "Found 0 unannotated drivers."
 puts $f "Found 0 partially unannotated drivers."
 close $f
}
proc report_checks {args} {}
proc report_check_types {args} {}
proc report_tns {args} {set f [open [lindex $args end] a]; puts $f "tns 0.0"; close $f}
proc report_worst_slack {flag redirect path} {
 set f [open $path a]; puts $f "worst slack [string range $flag 1 end] 0.5"; close $f
}
'''
    def run(container,cmd,**kw):
        script=Path(kw['marker']);wrapped=script.with_suffix('.fixture.tcl')
        wrapped.write_text(prelude+'\n'+script.read_text())
        p=subprocess.run(['tclsh',str(wrapped)],capture_output=True,text=True)
        return p.returncode,p.stdout,p.stderr
    monkeypatch.setattr(p3,'_docker_exec',run)
    assert s['emit']()
    text=s['rpt'].read_text()
    for c in ['SS','TT','FF']:
        for role in ['SETUP','HOLD']:assert f'=== {role} corner: process={c}' in text



# ── WHY THESE TWO TESTS EXEC SHIPPED SOURCE IN THE MODULE'S OWN NAMESPACE ────
# Both tests below take a BLOCK of `phase3_one_shot_runner.py` between two of
# its comment markers and run it, so the claim is about the bytes the flow
# actually ships rather than a paraphrase of them. The block is module-level
# code: at runtime it resolves helpers and constants in the MODULE's globals.
# Seeding `scope` from `vars(p3)` is therefore the faithful namespace, and the
# per-test entries below it are only the LOCALS the block reads, which still
# win as overrides.
#
# MEASURED 2026-09-21 -- what a hand-built namespace costs. `scope` used to be
# built from scratch, so every helper the block STARTED calling turned this
# contract test into a NameError at `<string>:<line>` that names a symbol the
# reader cannot place. Two landings did exactly that and neither was at fault:
#   20ae6cc0e (#2391, land/batch-SUBH) added
#       _alt, _alt_basis = canonical_post_route_sta(sta_out.parent, _RUN_STARTED_AT)
#     -> NameError: name 'canonical_post_route_sta' is not defined
#   f43e2b48b (#2407, land/batch-SUBP) added the module constant _RUN_STARTED_AT
#     -> the SECOND missing name, behind the first
# Naming them one at a time is whack-a-mole; the namespace is the root cause.
# The assertions are unchanged -- this fixes WHERE the block's names come from,
# not what the test claims.

def test_caller_refuses_failed_attempt_with_old_report(scene):
    import inspect,textwrap
    s=scene;s['rpt'].parent.mkdir(parents=True,exist_ok=True);s['rpt'].write_text('old report')
    source=Path(p3.__file__).read_text()
    block=source.split('    # --- Step 23: SPEF-based post-route STA (#527)')[1].split('    # --- TAPEOUT-SIGNOFF P1: multi-corner SPEF')[0]
    block=block[block.index('    spef_sta_rpt ='):]
    scope=dict(vars(p3),sta_out=s['rpt'].parent,spef_out=s['spef'],primary_def=s['spef'],project=s['tmp_path'],top='dut',pdk=s['pdk'],container='fixture',notes=[],written=[],rpt_phase3=s['tmp_path'],_signoff_regen=lambda *a:True,_emit_spef_sta=lambda *a:False)
    exec(textwrap.dedent(block),scope)
    assert not scope['spef_sta_ok']


def test_declared_aocv_not_silently_downgraded(scene,monkeypatch):
    monkeypatch.setattr(p3,'_discover_aocv_table',lambda *a:'declared.aocv')
    assert not scene['emit']()
    assert not scene['calls']


def test_execution_receipt_hashes_its_own_process_inputs(scene,monkeypatch):
    original=p3._docker_exec
    def run(*a,**k):
        c=Path(k['marker']).stem.rsplit('_',1)[1].upper()
        inputs={str(p) for p in k['inputs']}
        assert scene['by'][c] in inputs
        assert str(scene['tmp_path']/f'io__{c.lower()}_25C_1v00.lib') in inputs
        return original(*a,**k)
    monkeypatch.setattr(p3,'_docker_exec',run)
    assert scene['emit']()


import pytest


# ── RE-PINNED 2026-09-21 under R-0915-101: "a refused attempt leaves NO alias"
# is no longer the contract, and the landing that changed it said why.
#
# 20ae6cc0e (#2391, land/batch-SUBH) gave the refusal branch a FALLBACK: when the
# single-corner SPEF STA refuses itself, the run still has its multi-corner OCV
# report, and leaving the alias absent made `sta_signoff` report "No STA report
# found" about a run that HAD measured its timing (measured on r46). The alias
# now names its basis in an `STA_ALIAS_BASIS:` header, and `canonical_post_route_sta`
# refuses any basis older than `_RUN_STARTED_AT`, so a previous run's report can
# never be republished under a fresh name.
#
# The old single assertion could not express that, and its staging made it worse:
# it left `sta/sta_spef_based.rpt` fresh and non-empty in the REFUSED arm. That
# file IS the single-corner report whose attempt supposedly refused itself, and
# it is the FIRST canonical basis -- so the arm made the flow fall back onto the
# very report it had just refused, a state the producer never leaves behind (the
# two sibling tests above pin that it does not).
#
# Split into the three claims the shipped block actually makes. Nothing is
# dropped: "a refusal with no other basis leaves no alias" survives verbatim as
# its own case, and each case now also pins that the PRIOR run's bytes never
# stand for this attempt.
def _alias_block(s, *, success, other_basis=None):
    """Run the shipped canonical-alias block. Returns (alias path, scope)."""
    import textwrap
    sta=s['rpt'].parent;sta.mkdir(parents=True,exist_ok=True)
    alias=sta/'post_route_timing.rpt';alias.write_text('# SPEF-BASED old complete')
    if success:
        s['rpt'].write_text('fresh measured bytes')
    elif s['rpt'].is_file():
        s['rpt'].unlink()
    if other_basis in ('mcorner','mcorner_stale'):
        m=sta/'sta_mcorner_ocv.rpt';m.write_text('mcorner measured bytes')
        if other_basis=='mcorner_stale':
            # A report from an EARLIER run: same name, older than this process.
            import os;t=p3._RUN_STARTED_AT-600;os.utime(m,(t,t))
    source=Path(p3.__file__).read_text()
    block=source.split('    # --- Step 23: post-route STA report (canonical)')[1].split('    # --- #527: estimate-vs-SPEF discrepancy')[0]
    block=block[block.index('    post_route_rpt ='):]
    scope=dict(vars(p3),sta_out=sta,spef_sta_attempt_ok=success,spef_sta_ok=success,spef_sta_rpt=s['rpt'],postroute_timing_repair_out=sta,spef_out=s['spef'],project=s['tmp_path'],primary_sta=sta/'absent',written=[],notes=[])
    exec(textwrap.dedent(block),scope)
    return alias,scope


def _archived(alias):
    return [q.name for q in alias.parent.glob(alias.name+'.previous-*')]


def test_canonical_alias_tracks_current_attempt(scene):
    alias,_=_alias_block(scene,success=True)
    text=alias.read_text()
    assert 'fresh measured bytes' in text and 'SPEF-BASED' in text
    assert 'old complete' not in text, 'the previous run\'s bytes stood for this attempt'
    assert _archived(alias), 'the prior alias was not archived for diagnosis'


def test_a_refusal_with_no_other_basis_of_this_run_leaves_no_alias(scene):
    # The surviving half of the old claim, stated as its own case: a refused
    # attempt is never a basis, and nothing else this run wrote is one either.
    alias,_=_alias_block(scene,success=False)
    assert not alias.exists()
    assert _archived(alias), 'the prior alias was not archived for diagnosis'


def test_a_refusal_publishes_another_basis_of_this_run_and_names_it(scene):
    alias,scope=_alias_block(scene,success=False,other_basis='mcorner')
    text=alias.read_text()
    assert 'STA_ALIAS_BASIS: sta_mcorner_ocv.rpt' in text, text[:300]
    assert 'mcorner measured bytes' in text
    assert 'old complete' not in text, 'the previous run\'s bytes stood for this attempt'
    assert any('sta_mcorner_ocv.rpt' in n for n in scope['notes']), scope['notes']


def test_a_basis_older_than_this_run_is_refused_not_republished(scene):
    """`canonical_post_route_sta`'s `not_before` half, which is the whole point
    of it: a report carrying an earlier run's bytes must not be republished
    under a freshly-written alias name whose timestamp asserts a freshness the
    content does not have. Measured on r46/lane icsub2: three arms re-entered
    phase 3 on a copy, wrote no new STA, and the alias served an 02:53 basis
    under an 11:10 mtime."""
    alias,scope=_alias_block(scene,success=False,other_basis='mcorner_stale')
    assert not alias.exists(), alias.read_text()[:300]
    assert not scope['notes'], scope['notes']
