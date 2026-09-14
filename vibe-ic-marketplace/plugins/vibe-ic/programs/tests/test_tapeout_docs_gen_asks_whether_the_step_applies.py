"""One gate clause, two programs, and only one of them was asking (icspm2).

MEASURED FAILURE THIS PINS
==========================
Step 37.5ic's gate clause runs `tapeout_precheck` and `tapeout_docs_gen` inside
one `all_of`. On a gf180mcuD run of `spm` — a HARDMACRO delivery whose step
0.5ic routed it to the IP terminal:

    tapeout_precheck . --json …   -> PASS, step_applies false
        "step 37.5ic does not apply to this design: step 0.5ic routed it to the
         IP/hardmacro terminal … which delivers LEF/Liberty/GDS/Verilog and no
         die. There is no submission, so there is no tape-out precheck to pass"

    phase3_one_shot_runner.step_tapeout_docs_gen -> SKIP
        from `_canonical_step_condition(project, "37.5ic")`

    tapeout_docs_gen --project . --out-dir reports/phase3/docs  -> rc 1
        "NOT RELEASABLE — no documents written. 5 propert(ies) are not clean:
           - Routing DRC / Magic DRC / KLayout DRC / Density / GDS-vs-layout XOR
             … NOT_MEASURED"

Two of the three sites knew the design has no die; this one refused it on five
DIE metrics. The flow's structural condition cannot separate the routes either —
step 0.5ic writes `input/submission_template/slots/*.yaml` on EVERY route — so
the question has to be asked where the answer lives, which is the design's own
declaration.

WHAT MUST NOT CHANGE
====================
`delivery_route` answers UNDECLARED, never IP, when nobody declared a route, and
its own docstring says why: "0.5ic failing is the one circumstance in which a
CHIP most needs this step, and reading its silence as 'must be an IP' would let
the failure delete the check that would have reported it." So a CHIP and an
UNDECLARED design with unmeasured DRC are still rc 1 NOT RELEASABLE. That is
what the second half of this file is.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import tapeout_docs_gen as tdg       # noqa: E402
import tapeout_precheck as tp        # noqa: E402

CLEAN = {
    "route__drc_errors": 0, "magic__drc_error__count": 0,
    "klayout__drc_error__count": 0, "klayout__density_error__count": 0,
    "design__xor_difference__count": 0, "design__lvs_error__count": 0,
    "design__lvs_unmatched_device__count": 0,
    "design__lvs_unmatched_net__count": 0,
    "design__lvs_unmatched_pin__count": 0,
    "antenna__violating__nets": 0, "antenna__violating__pins": 0,
    "timing__setup__ws": 0.42, "timing__setup__tns": 0.0,
    "timing__hold__ws": 0.1, "timing__hold__tns": 0.0,
    "design__max_slew_violation__count": 0,
    "design__max_cap_violation__count": 0,
    "design__die__bbox": "0 0 206 206",
}
#: The state the measured run was in: five die properties unmeasured.
UNMEASURED = dict(CLEAN, **{k: "NOT_MEASURED" for k in (
    "route__drc_errors", "magic__drc_error__count",
    "klayout__drc_error__count", "klayout__density_error__count",
    "design__xor_difference__count")})


def _project(tmp_path, route, metrics):
    p = tmp_path / "proj"
    (p / "phase3/final").mkdir(parents=True)
    (p / "phase3/final/metrics.json").write_text(json.dumps(metrics))
    tpl = p / "input/submission_template"
    tpl.mkdir(parents=True)
    if route == "IP":
        # What step 0.5ic writes on the IP terminal, alongside the four slot
        # yamls it writes on EVERY route.
        (tpl / "NO_TEMPLATE.txt").write_text("IP terminal\n")
    elif route == "CHIP":
        (tpl / "SELF_TAPEOUT.txt").write_text("self tape-out\n")
    return p


def _run(project, out_dir, *extra):
    cp = subprocess.run(
        [sys.executable, str(PROGRAMS / "tapeout_docs_gen.py"),
         "--project", str(project), "--out-dir", str(out_dir), *extra],
        capture_output=True, text=True)
    return cp.returncode, cp.stdout + cp.stderr


# ---------------------------------------------------------------------------
# DIRECTION 1 — the IP route is NOT_APPLICABLE, on the rc-0 channel
# ---------------------------------------------------------------------------
def test_the_ip_route_is_not_applicable_and_writes_nothing(tmp_path):
    p = _project(tmp_path, "IP", UNMEASURED)
    assert tp.delivery_route(p)[0] == tp.ROUTE_IP
    out = tmp_path / "docs"
    rc, text = _run(p, out)
    assert rc == 0, text
    assert "NOT_APPLICABLE" in text
    assert "NOT RELEASABLE" not in text
    assert not out.exists(), "an inapplicable step must write no document"


def test_the_refusal_names_the_route_evidence_it_read(tmp_path):
    """A verdict a reader cannot re-derive is the one that gets worked around."""
    p = _project(tmp_path, "IP", UNMEASURED)
    _rc, text = _run(p, tmp_path / "docs")
    assert "IP/hardmacro terminal" in text
    assert "37.5ip" in text, "it must say where the IP documents DO come from"


def test_the_ip_route_is_not_applicable_even_when_everything_is_clean(tmp_path):
    """Applicability is decided before content, exactly as `tapeout_precheck`
    decides it: 'STEP-LEVEL APPLICABILITY, DECIDED FIRST'."""
    p = _project(tmp_path, "IP", CLEAN)
    out = tmp_path / "docs"
    rc, text = _run(p, out)
    assert rc == 0
    assert "NOT_APPLICABLE" in text
    assert not out.exists()


# ---------------------------------------------------------------------------
# DIRECTION 2 — every other route is unchanged
# ---------------------------------------------------------------------------
def test_a_self_tapeout_chip_with_unmeasured_metrics_is_still_refused(tmp_path):
    p = _project(tmp_path, "CHIP", UNMEASURED)
    assert tp.delivery_route(p)[0] == tp.ROUTE_CHIP
    rc, text = _run(p, tmp_path / "docs")
    assert rc == 1, text
    assert "NOT RELEASABLE" in text


def test_an_undeclared_route_is_still_refused(tmp_path):
    """The load-bearing control. Silence is NOT the IP path — reading it as one
    would delete the check a design most needs when 0.5ic failed."""
    p = _project(tmp_path, "NONE", UNMEASURED)
    assert tp.delivery_route(p)[0] == tp.ROUTE_UNDECLARED
    rc, text = _run(p, tmp_path / "docs")
    assert rc == 1, text
    assert "NOT RELEASABLE" in text


def test_a_clean_chip_still_writes_its_documents(tmp_path):
    p = _project(tmp_path, "CHIP", CLEAN)
    out = tmp_path / "docs"
    rc, text = _run(p, out)
    assert rc == 0, text
    assert out.is_dir() and list(out.glob("*.html")), sorted(
        q.name for q in out.iterdir()) if out.is_dir() else text


def test_metrics_without_a_project_does_not_consult_a_route(tmp_path):
    """`--metrics` alone has no project to read a declaration from; the
    behaviour there must be byte-identical to before."""
    m = tmp_path / "m.json"
    m.write_text(json.dumps(UNMEASURED))
    cp = subprocess.run(
        [sys.executable, str(PROGRAMS / "tapeout_docs_gen.py"),
         "--metrics", str(m), "--out-dir", str(tmp_path / "d")],
        capture_output=True, text=True)
    assert cp.returncode == 1
    assert "NOT RELEASABLE" in cp.stdout + cp.stderr


def test_allow_incomplete_still_writes_a_draft_for_a_chip(tmp_path):
    p = _project(tmp_path, "CHIP", UNMEASURED)
    out = tmp_path / "docs"
    rc, text = _run(p, out, "--allow-incomplete")
    assert rc == 0, text
    assert out.is_dir() and list(out.glob("*.html"))


# ---------------------------------------------------------------------------
# The SAME question, one program further along the same gate clause
# ---------------------------------------------------------------------------
def test_an_ip_delivery_owes_no_IC_release_documentation(tmp_path):
    """MEASURED after the fix above moved the clause one program along:

        release_docs_check . --arm ic   rc 1
          [ERROR] RELEASE_DOCUMENTATION_ABSENT (spm): the run signs off layout
          `spm` under phase3/stage4/gds and phase3/stage4/documentation/ic/spm
          carries no release documentation
        release_docs_check . --arm ip   rc 0   (six documents, PASS)

    A hardmacro kit SHIPS a GDS — it is one of its four views — so a
    stem-per-GDS rule makes every IP delivery owe a DIE document set it has no
    die for, while the IP arm has already documented the same artefacts."""
    import _ic_release_artefacts as art
    p = _project(tmp_path, "IP", CLEAN)
    (p / "phase3/stage4/gds").mkdir(parents=True)
    (p / "phase3/stage4/gds/spm.gds").write_bytes(b"\x00\x06\x00\x02\x00\x07")
    assert art.releases(p) == [], "an IP delivery owes no IC release"


def test_a_self_tapeout_chip_still_owes_its_release_documentation(tmp_path):
    import _ic_release_artefacts as art
    p = _project(tmp_path, "CHIP", CLEAN)
    (p / "phase3/stage4/gds").mkdir(parents=True)
    (p / "phase3/stage4/gds/spm.gds").write_bytes(b"\x00\x06\x00\x02\x00\x07")
    assert art.releases(p) == ["spm"]


def test_an_undeclared_route_still_owes_its_release_documentation(tmp_path):
    """Silence is not the IP path — a chip whose 0.5ic failed still owes them."""
    import _ic_release_artefacts as art
    p = _project(tmp_path, "NONE", CLEAN)
    (p / "phase3/stage4/gds").mkdir(parents=True)
    (p / "phase3/stage4/gds/spm.gds").write_bytes(b"\x00\x06\x00\x02\x00\x07")
    assert art.releases(p) == ["spm"]
