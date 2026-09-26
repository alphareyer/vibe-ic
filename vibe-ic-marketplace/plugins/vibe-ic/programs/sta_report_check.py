#!/usr/bin/env python3
"""STA report check — wrapper for eda_report_audit --mode sta.

ARGV WAS DISCARDED. This wrapper used to rebuild the command line as
``main([sys.argv[1], "--mode", "sta"])``, which threw away every other flag
the caller passed — including the ``--json <path>`` the flow definition
declares for step 10 and step 23.

That is not cosmetic. ``reports/phase3/sta/post_route_summary.json`` is
step 23's own ``required_outputs`` entry and this wrapper is its ONLY
declared producer anywhere in the plugin; the same holds for step 10's
``reports/phase3/sta/pre_pnr_summary.json``. Because the flag was dropped,
neither file was ever written — by this program or any other — so a
declared sign-off output had no producer at all, on any run.

Measured before the change (real completed run
``campaign_pr427/spm/converge_ihp-sg13g2``)::

    $ sta_report_check . --mode phase3/stage3/sta \
          --json reports/phase3/sta/post_route_summary.json
    rc=0
    $ ls reports/phase3/sta/post_route_summary.json
    No such file or directory

The caller's argv is now forwarded verbatim. ``--mode sta`` is supplied only
when the caller states no mode, which preserves the bare
``sta_report_check <project>`` call shape used by
``programs/tests/test_report_wrappers.py`` and ``test_sta_report_check.py``.

A mode the caller DOES state is passed through to argparse rather than
silently replaced: a declaration naming a mode that is not an
``eda_report_audit`` choice is a broken declaration and must be visible, not
absorbed. (The flow yaml declared ``--mode phase3/stage3/sta``, which is a
path, not a mode; it is corrected in the same change, and
``test_step23_25_signoff_gates_wired.py::test_flow_declares_a_real_report_mode``
asserts every in-repo declaration names a real mode so it cannot drift back.)

PR #473 changed only this wrapper and ``em_report_check.py`` and stated that the
identically shaped ``ir_drop_report_check`` / ``antenna_report_check`` /
``drc_report_check`` wrappers were "left alone deliberately". They no longer
are: the medium/low backlog follow-up measured each one's blast radius and
forwarded argv in all three, with their own output-path collisions (steps 24 and
26 both declared the PRODUCER's file) fixed in the same change. See
``test_wrapper_argv_forwarding.py``. ``lvs_report_check.py`` is a different
shape — it does its own pre-checks and is out of that change's scope.

STEP 23 ON THE TOOL (F15). When step 23 runs `librelane` or `dual`
(`phase3/librelane_switch.json`) and the caller scopes this audit to the
post-route side (`--under` names a post-route path), the subject is the
TOOL's sign-off: every corner report `OpenROAD.STAPostPNR` wrote
(`<corner>/vibeic_signoff.rpt`, read through
`librelane_signoff.step23_tool_arm`, each bound to the sha256 the tool wrote
and timed on the views still on disk), passed to the audit as `--subject`
under the tool's own step directory. The direct deck's
`post_route_timing.rpt` is not read. A corner report that is missing,
unbound or stale REFUSES (rc 1, verdict REFUSED) before the audit runs. The
pre-layout (step 10) call and `direct` mode are unchanged.

ENFORCEMENT: blocking — `phase3_one_shot_runner.step_declared_signoff_gates`
invokes this gate inline and a non-zero exit fails the run. The declaration is
stated so `flow_gate_enforcement_audit` reports a CONTRADICTION if the wiring
is ever removed while the claim stays.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eda_report_audit import main  # noqa: E402
import _sta_basis  # noqa: E402
import librelane_signoff as _ls  # noqa: E402 — step 23 on the tool (F15)
from _atomic_artefact import write_text as _atomic_write_text  # noqa: E402
from librelane_contract import Refusal  # noqa: E402

MODE = "sta"


def build_argv(caller_argv):
    """Forward the caller's argv, defaulting the mode this wrapper names."""
    argv = list(caller_argv)
    if not argv:
        argv = ["."]
    if not any(a == "--mode" or a.startswith("--mode=") for a in argv):
        argv += ["--mode", MODE]
    return argv


def _split(argv):
    """``(project, [--under values], [every other arg])``.  The project is
    the leading positional, which is how every caller states it."""
    project = argv[0] if argv and not argv[0].startswith("-") else None
    unders, rest, i = [], [], 0
    while i < len(argv):
        if argv[i] == "--under" and i + 1 < len(argv):
            unders.append(argv[i + 1])
            i += 2
            continue
        if argv[i].startswith("--under="):
            unders.append(argv[i].split("=", 1)[1])
        else:
            rest.append(argv[i])
        i += 1
    return project, unders, rest


def tool_argv(argv):
    """The argv that audits step 23's TOOL arm, or None to audit as asked.

    None unless the scope is the post-route side and step 23 runs on the
    tool.  Raises `Refusal` when the tool's corner reports cannot be read."""
    project, unders, rest = _split(argv)
    post = _sta_basis.BASIS_TOKENS["POST_ROUTE"]
    if project is None or not unders or not all(
            any(t in u.lower() for t in post) for u in unders):
        return None
    root = Path(project)
    arm = _ls.step23_tool_arm(root)
    if arm is None:
        return None
    base = root.resolve()
    out = list(rest) + ["--under", str(Path(arm["folder"]).resolve().relative_to(base))]
    for row in arm["corners"].values():
        out += ["--subject", str(Path(row["files"][_ls.CORNER_REPORT]["path"])
                                 .resolve().relative_to(base))]
    return out


def _refuse(argv, exc):
    """A refusal is written where the verdict would have been, and is rc 1."""
    doc = {"program": "eda_report_audit:sta", "passed": False,
           "verdict": "REFUSED", "refusal": getattr(exc, "code", None),
           "findings": [{"rule": "STA_TOOL_ARM_UNREADABLE", "severity": "ERROR",
                         "message": f"step 23 runs on the tool and its sign-off "
                                    f"cannot be read: {exc}"}]}
    if "--json" in argv and argv.index("--json") + 1 < len(argv):
        out = Path(argv[argv.index("--json") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(out, json.dumps(doc, indent=2) + "\n")
    print(json.dumps(doc, indent=2))
    return 1


def _run_and_emit(caller_argv):
    """Run the audit, then hand its outcome to the one metrics schema.

    step_metrics_adoption_check: a step that declares this program must emit
    through `step_metrics`. The step is read from the flow clause that ran it
    (program + `--json` path), never assumed -- this wrapper is step 10's gate
    and step 23's. Best-effort by construction: the metric can never change
    the gate's rc.
    """
    argv = build_argv(caller_argv)
    try:
        tool = tool_argv(argv)
    except Refusal as exc:
        tool, rc = None, _refuse(argv, exc)
    else:
        rc = main(tool if tool is not None else argv)
    import step_metrics  # noqa: PLC0415
    step_metrics.emit_gate_outcome(Path(__file__).stem, argv, rc)
    return rc


if __name__ == "__main__":
    sys.exit(_run_and_emit(sys.argv[1:]))
