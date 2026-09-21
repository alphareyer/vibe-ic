"""R-0915-107 -- `publish_si_sta_report` reads `corners` as the MAPPING it is.

MY BUG, from the lane that landed this publisher. `run()` builds

    corners_out: Dict[str, dict] = {}
    corners_out[corner] = {... "sta_rc": rc ...}

and the publisher was written as if that were a LIST of dicts:

    if nom_rc != 0 or not any(c.get("sta_rc") == 0 for c in corners):

Iterating a dict yields its KEYS, so `c` is `str` and `c.get` raises
AttributeError -- on every run, before the report could be written.

MEASURED on the spm run18L run (plugin 1.23.20, a run whose DESIGN PASSED: DRC 0,
LVS PASS, STA PASS, 9/9 sign-off): `nominal.sta_rc` 0 and both corners `sta_rc` 0,
so the guard should have let the report through. Instead the JSON was written, the
publisher raised, the shipping caller ignored the subprocess rc, and step 27 FAILed
`missing_artefact` on `reports/phase3/si_mcf_sta.rpt` -- the report this function
exists to publish -- with the traceback appearing in no report, no step record and
no log in the whole run tree.

So this file pins the mapping shape, and a list is accepted too, because a caller
that passes one must not become a second silent failure.
"""
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import si_mcf_sta as S                                   # noqa: E402

_MAPPING = {
    "setup": {"corner": "setup", "sta_rc": 0, "mcf_worst": 2.0,
              "worst_slack_before_ns": 1.0, "worst_slack_after_ns": 0.9,
              "delta_ns": 0.1},
    "hold": {"corner": "hold", "sta_rc": 0, "mcf_worst": 0.0,
             "worst_slack_before_ns": 0.5, "worst_slack_after_ns": 0.5,
             "delta_ns": 0.0},
}


def _publish(tmp_path, corners, nom_rc=0):
    oj = tmp_path / "reports" / "phase3" / "si_mcf_sta.json"
    oj.parent.mkdir(parents=True, exist_ok=True)
    oj.write_text("{}\n")
    return S.publish_si_sta_report(
        tmp_path, oj, str(oj), top="chip_top", spef_name="x.spef",
        verdict="ADVISORY", nom_setup=None, nom_hold=None, nom_rc=nom_rc,
        corners=corners)


# -- the shape `run()` actually passes ----------------------------------------
def test_a_corner_mapping_publishes_the_stamped_report(tmp_path):
    """THE RED: this raised AttributeError and wrote nothing."""
    got = _publish(tmp_path, _MAPPING)
    assert got is not None and Path(got).is_file(), got
    text = Path(got).read_text()
    assert "STA_BASIS: POST_ROUTE_MCF_SPEF" in text, text[:200]
    # both corners reach the report, so the mapping was iterated by VALUE
    assert "setup" in text and "hold" in text


def test_the_report_carries_every_corner_the_mapping_holds(tmp_path):
    got = _publish(tmp_path, _MAPPING)
    body = Path(got).read_text()
    mcf_lines = [l for l in body.splitlines() if l.startswith("mcf ")]
    assert len(mcf_lines) == len(_MAPPING), mcf_lines


# -- a list is still accepted, so no caller becomes the next silent failure ---
def test_a_corner_list_publishes_too(tmp_path):
    got = _publish(tmp_path, list(_MAPPING.values()))
    assert got is not None and Path(got).is_file()
    assert "STA_BASIS:" in Path(got).read_text()


# -- the decline is UNCHANGED: no basis, no report ----------------------------
def test_no_corner_reached_opensta_still_writes_nothing(tmp_path):
    """The guard this publisher was built around, untouched: a timing report
    with no basis is the laundering the stage gate exists to catch."""
    dead = {k: dict(v, sta_rc=1) for k, v in _MAPPING.items()}
    assert _publish(tmp_path, dead) is None
    assert not (tmp_path / "reports" / "phase3" / "si_mcf_sta.rpt").exists()


def test_a_failed_nominal_still_writes_nothing(tmp_path):
    assert _publish(tmp_path, _MAPPING, nom_rc=1) is None


def test_an_empty_corner_set_writes_nothing(tmp_path):
    assert _publish(tmp_path, {}) is None
    assert _publish(tmp_path, []) is None
