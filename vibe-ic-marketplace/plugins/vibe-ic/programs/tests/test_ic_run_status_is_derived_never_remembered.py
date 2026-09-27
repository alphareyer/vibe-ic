#!/usr/bin/env python3
"""`ic_run_status_derive` — the status of an IC is DERIVED from the run, or it
is NOT_MEASURED. There is no third source.

WHAT IS PINNED, AND WHY EACH ONE IS HERE
----------------------------------------
The register this program replaces was a hand-fed marker directory an
acceptance script counted, and it went stale exactly as the repo's own earlier
ruling said a hand-maintained register must. So the load-bearing test here is
the MUTATION CONTROL: plant a marker that says PASS beside a run that says
FAIL, and the program must still say FAIL. A version that consults the marker
fails that test — which is the only reason the test is worth its bytes.

The other four pin the refusals, which matter more than the passes:

  * a run whose own report says FAIL is reported FAIL, never softened;
  * a run root whose report cannot be PARSED is NOT_MEASURED with the reason
    named — never "no violations", never a pass;
  * a declared IC with no run under the search roots is NO_RUN, a row of its
    own, because absence with no row is the state one IC sat in unnoticed;
  * prose that claims more than the machine verdict is PRINTED as a
    disagreement, and the run wins.

Every check is proven BOTH DIRECTIONS: each fixture is also built in the shape
that must NOT trip it, and the test asserts the quiet direction too.

Fixture IC names are synthetic (`unit_one`, `unit_two`) — no IC, PDK, vendor or
SKU literal, per `source_chip_agnostic_check`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
PROG = PROGRAMS / "ic_run_status_derive.py"

sys.path.insert(0, str(PROGRAMS))
import _eda_pin  # noqa: E402
from _stated_eda_image import state_the_image_for_children  # noqa: E402


@pytest.fixture(autouse=True)
def _the_stated_image(monkeypatch):
    """The pin is STATED, here and in the program each test spawns (lane
    rfimg2). Unstated, the child read it from THIS HOST's docker: inside the
    image (no docker) every test that runs the program went red on a
    traceback, and on a host the ON_PIN arm compared against whatever that
    host happened to hold."""
    state_the_image_for_children(monkeypatch)


# --------------------------------------------------------------------------
# fixtures: a run root is what a run PUBLISHED

def make_run(root: Path, *, ic_name="unit_one", verdict="FAIL", halted_at="phase3",
             phases=None, audit_verdict="FAIL", run_at="2026-01-01T00:00:00+00:00",
             prose=None, image=None, drc=None, bad_audit=False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    docs = root / "phase1" / "generated_docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "L1_DATASHEET.json").write_text(json.dumps({"ic_name": ic_name}))

    orch = root / "reports" / "orchestrator"
    orch.mkdir(parents=True, exist_ok=True)
    (orch / "vibe_ic_one_shot.json").write_text(json.dumps({
        "phase": "vibe-ic", "halted_at": halted_at, "verdict": verdict,
        "phases": phases if phases is not None else [
            {"name": "phase1", "verdict": "PASS", "rc": 0},
            {"name": "phase3", "verdict": verdict, "rc": 1},
        ],
    }))

    audit = root / "reports" / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    if bad_audit:
        (audit / "phase23_completion_audit.json").write_text("{ this is not JSON")
    else:
        (audit / "phase23_completion_audit.json").write_text(json.dumps({
            "verdict": audit_verdict, "run_at": run_at,
            "step_counts": {"PASS": 1, "FAIL": 1},
        }))

    if prose is not None:
        (root / "RESULT.md").write_text(prose)
    if image is not None:
        (root / "reports" / "container_image.json").write_text(json.dumps(image))
    if drc is not None:
        p3 = root / "reports" / "phase3"
        p3.mkdir(parents=True, exist_ok=True)
        (p3 / "drc_signoff.rpt").write_text(drc["rpt"])
        (p3 / "drc_signoff.json").write_text(json.dumps({
            "program": "eda_report_audit:drc", "passed": drc["count"] == 0,
            "summary": {"real_violation_total": drc["count"],
                        "scoped_under": ["reports/phase3/drc_signoff.rpt"]},
        }))
    return root


RDB = """<?xml version="1.0"?>
<report-database>
 <items>
  <item><category>'R1.1'</category><cell>u</cell></item>
  <item><category>'R2.2'</category><cell>u</cell></item>
  <item><category>'R1.1'</category><cell>u</cell></item>
 </items>
</report-database>
"""


def run(*argv, env=None):
    return subprocess.run([sys.executable, str(PROG), *argv],
                          capture_output=True, text=True, env=env)


def rows(*argv, env=None):
    p = run(*argv, "--json", env=env)
    assert p.returncode in (0, 1, 2), p.stderr
    return json.loads(p.stdout), p.returncode


# --------------------------------------------------------------------------
# 1. a FAIL is reported FAIL — and a PASS is reported PASS (both directions)

def test_a_run_that_says_fail_is_never_reported_as_anything_else(tmp_path):
    make_run(tmp_path / "r", verdict="FAIL")
    doc, rc = rows("--run-root", str(tmp_path / "r"))
    (row,) = doc["rows"]
    assert row["status"] == "RESOLVED"
    assert row["verdict"] == "FAIL"
    assert row["verdict_scope"] == "whole-run"
    # Reporting a FAIL is a SUCCESSFUL measurement: this program states status,
    # it does not grade it.
    assert rc == 0


def test_the_verdict_column_is_the_runs_own_word(tmp_path):
    """The quiet direction. Nothing here invents PASS; it copies what is there."""
    make_run(tmp_path / "r", verdict="PASS_WITH_WAIVERS", audit_verdict="PASS")
    doc, rc = rows("--run-root", str(tmp_path / "r"))
    assert doc["rows"][0]["verdict"] == "PASS_WITH_WAIVERS"
    assert rc == 0


def test_a_phase_scoped_verdict_is_labelled_with_its_scope(tmp_path):
    """A phase-2 runner's PASS is a statement about phase 2 only. Printing it
    unlabelled beside a whole-run verdict is how a partial run reads as a chip."""
    r = make_run(tmp_path / "r", verdict="FAIL")
    (r / "reports/orchestrator/vibe_ic_one_shot.json").unlink()
    (r / "reports/orchestrator/phase2_one_shot.json").write_text(
        json.dumps({"verdict": "PASS_WITH_WAIVERS", "phases": []}))
    doc, _ = rows("--run-root", str(r))
    assert doc["rows"][0]["verdict"] == "PASS_WITH_WAIVERS"
    assert doc["rows"][0]["verdict_scope"] == "phase2-only"


# --------------------------------------------------------------------------
# 2. unreadable is NOT_MEASURED with the reason — never a default

def test_an_unparsable_audit_is_not_measured_and_names_the_reason(tmp_path):
    make_run(tmp_path / "r", bad_audit=True)
    doc, rc = rows("--run-root", str(tmp_path / "r"))
    audit = doc["rows"][0]["audit"]
    assert audit["status"] == "NOT_MEASURED"
    assert "unparsable" in audit["reason"] and "phase23_completion_audit" in audit["reason"]
    # and no figure was invented in its place
    assert "verdict" not in audit and "step_counts" not in audit


def test_a_directory_that_published_no_run_is_not_measured_not_empty(tmp_path):
    (tmp_path / "empty").mkdir()
    doc, rc = rows("--run-root", str(tmp_path / "empty"))
    (row,) = doc["rows"]
    assert row["status"] == "NOT_MEASURED"
    assert "no run artefact" in row["reason"]
    assert rc == 2, "an unreadable population is UNDETERMINED, never a pass"


def test_an_absent_signoff_report_is_not_measured_never_zero(tmp_path):
    """The direction that matters: no DRC report must never read as `drc=0`."""
    make_run(tmp_path / "r")                      # no drc artefacts at all
    doc, _ = rows("--run-root", str(tmp_path / "r"))
    drc = doc["rows"][0]["signoff"]["drc"]
    assert drc["status"] == "NOT_MEASURED" and "drc_signoff.json" in drc["reason"]
    assert "count" not in drc


def test_a_drc_report_that_is_present_yields_count_and_rule_names(tmp_path):
    """The other direction: present and readable, so it must be measured."""
    make_run(tmp_path / "r", drc={"count": 3, "rpt": RDB})
    doc, _ = rows("--run-root", str(tmp_path / "r"))
    drc = doc["rows"][0]["signoff"]["drc"]
    assert drc["status"] == "READ" and drc["count"] == 3
    assert drc["rules"] == ["R1.1", "R2.2"], "the RULE SET, deduplicated"


def test_an_unreadable_drc_report_does_not_become_an_empty_rule_set(tmp_path):
    r = make_run(tmp_path / "r", drc={"count": 3, "rpt": RDB})
    (r / "reports/phase3/drc_signoff.rpt").unlink()
    doc, _ = rows("--run-root", str(r))
    drc = doc["rows"][0]["signoff"]["drc"]
    assert drc["count"] == 3
    assert drc["rules"] == [], "no rule was read"
    assert drc["rules_not_measured"], "and the program says so rather than implying none"


# --------------------------------------------------------------------------
# 3. NO_RUN — the state that had no row

def test_a_declared_ic_with_no_run_is_no_run(tmp_path):
    make_run(tmp_path / "runs" / "a", ic_name="unit_one")
    doc, rc = rows("--root", str(tmp_path / "runs"), "--ic", "unit_one", "--ic", "unit_two")
    by = {r.get("declared_ic"): r for r in doc["rows"] if r["status"] == "NO_RUN"}
    assert set(by) == {"unit_two"}
    assert "no run under the search roots states this ic_name" in by["unit_two"]["reason"]
    assert rc == 1


def test_a_declared_ic_that_does_have_a_run_produces_no_no_run_row(tmp_path):
    make_run(tmp_path / "runs" / "a", ic_name="unit_one")
    doc, rc = rows("--root", str(tmp_path / "runs"), "--ic", "unit_one")
    assert not [r for r in doc["rows"] if r["status"] == "NO_RUN"]
    assert rc == 0


# --------------------------------------------------------------------------
# 4. THE MUTATION CONTROL: a hand-fed marker must not be able to speak

def test_a_hand_fed_marker_claiming_pass_cannot_override_the_runs_fail(tmp_path, monkeypatch):
    """The register this program replaces was a marker directory under $HOME.

    Plant the most persuasive version of it — a marker file whose entire content
    is the IC's name and the word PASS, in the conventional place — beside a run
    whose own report says FAIL. A program that consults it reports PASS and this
    test goes red. That is the whole point of the test.
    """
    home = tmp_path / "home"
    markers = home / ".claude" / "fleet" / "icpass_markers"
    markers.mkdir(parents=True)
    (markers / "unit_one").write_text("PASS\n")
    (markers / "unit_one.json").write_text(json.dumps({"ic": "unit_one", "verdict": "PASS"}))

    make_run(tmp_path / "r", ic_name="unit_one", verdict="FAIL")
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["HOME"] = str(home)
    doc, _ = rows("--run-root", str(tmp_path / "r"), env=env)
    assert doc["rows"][0]["verdict"] == "FAIL"
    assert "PASS" not in json.dumps(doc["rows"][0]["verdict"])


def test_the_program_reads_nothing_outside_the_run_root_it_was_given(tmp_path):
    """The static half of the same control.

    Every path this program opens is composed from a run root, a search root, or
    `_eda_pin`. A source that names a register — any register — is the shape
    that went stale, so its absence is asserted rather than assumed.
    """
    src = PROG.read_text()
    body = src.split('"""', 2)[2]      # the docstring MAY discuss the register
    for banned in ("icpass", "markers", "expanduser", "Path.home"):
        assert banned not in body, f"{banned!r} appears in the executable body"


# --------------------------------------------------------------------------
# 5. prose loses, and the disagreement is printed

def test_prose_claiming_more_than_the_machine_verdict_is_printed_as_a_disagreement(tmp_path):
    make_run(tmp_path / "r", verdict="FAIL", prose="# result\n\nOVERALL: PRODUCTION-READY\n")
    doc, rc = rows("--run-root", str(tmp_path / "r"))
    row = doc["rows"][0]
    assert row["verdict"] == "FAIL", "the run wins"
    assert row["disagreements"] and "RESULT.md" in row["disagreements"][0]
    assert rc == 1
    text = run("--run-root", str(tmp_path / "r")).stdout
    assert "DISAGREEMENT" in text and "the run wins" in text


def test_prose_that_agrees_is_not_reported_as_a_disagreement(tmp_path):
    make_run(tmp_path / "r", verdict="FAIL", prose="# result\n\nOVERALL: FAIL\n")
    doc, rc = rows("--run-root", str(tmp_path / "r"))
    assert doc["rows"][0]["disagreements"] == []
    assert rc == 0


# --------------------------------------------------------------------------
# 6. every figure carries the image the run used

def test_a_run_on_the_current_pin_is_on_pin(tmp_path):
    make_run(tmp_path / "r", image={
        "image_ref": f"example.invalid/repo@{_eda_pin.IMAGE_DIGEST}"})
    doc, _ = rows("--run-root", str(tmp_path / "r"))
    img = doc["rows"][0]["image"]
    assert img["pin_state"] == "ON_PIN" and img["digest"] == _eda_pin.IMAGE_DIGEST


def test_a_run_on_another_digest_is_off_pin(tmp_path):
    other = "sha256:" + "0" * 64
    make_run(tmp_path / "r", image={"image_ref": f"example.invalid/repo@{other}"})
    doc, _ = rows("--run-root", str(tmp_path / "r"))
    assert doc["rows"][0]["image"]["pin_state"] == "OFF_PIN"


def test_a_recorded_local_image_id_is_not_comparable_rather_than_off_pin(tmp_path):
    """An Id is content-addressed by the storage driver: it can neither confirm
    nor deny the pin, and calling it a mismatch would be inventing a result."""
    make_run(tmp_path / "r", image={"image_ref": "repo:tag",
                                    "image_id": "sha256:" + "a" * 64})
    doc, _ = rows("--run-root", str(tmp_path / "r"))
    img = doc["rows"][0]["image"]
    assert img["pin_state"] == "NOT_COMPARABLE" and img["kind"] == "image_id"


def test_a_run_recording_no_image_is_unrecorded_never_assumed_on_pin(tmp_path):
    make_run(tmp_path / "r")
    doc, _ = rows("--run-root", str(tmp_path / "r"))
    assert doc["rows"][0]["image"]["pin_state"] == "UNRECORDED"


def test_the_pin_has_one_home(tmp_path):
    """The digest is never re-spelled here; it is read from `_eda_pin`."""
    assert _eda_pin.IMAGE_DIGEST not in PROG.read_text()
    doc, _ = rows("--run-root", str(make_run(tmp_path / "r")))
    assert doc["pin"] == _eda_pin.IMAGE_DIGEST


def test_an_unresolvable_pin_is_named_and_the_report_still_derives(tmp_path):
    """A STATUS REPORT NEEDS NO DOCKER (lane rfimg2). With no pin resolvable --
    no docker on PATH, nothing stated -- the program used to die on
    `ImageNotResolvable` and publish no row at all. Now every run is still
    reported, `pin` is null with the refusal named, and a recorded digest is
    NOT_COMPARABLE: neither on nor off a pin nobody could name."""
    import os                                                   # noqa: PLC0415
    make_run(tmp_path / "r", image={
        "image_ref": "example.invalid/repo@sha256:" + "5" * 64})
    empty = tmp_path / "no-docker-bin"
    empty.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k not in ("VIBEIC_EDA_IMAGE", "IIC_EDA_IMAGE")}
    env["PATH"] = str(empty)
    doc, _ = rows("--run-root", str(tmp_path / "r"), env=env)
    assert doc["pin"] is None
    assert _eda_pin.IMAGE_NOT_RESOLVABLE in (doc["pin_refusal"] or "")
    img = doc["rows"][0]["image"]
    assert img["pin_state"] == "NOT_COMPARABLE", img
    assert doc["rows"][0]["verdict"] == "FAIL", "the run's own word survives"


# --------------------------------------------------------------------------
# 7. identity is derived; a directory basename is not an identity

def test_a_run_that_states_no_ic_name_is_labelled_by_path_and_says_so(tmp_path):
    r = make_run(tmp_path / "some_dir_name")
    (r / "phase1/generated_docs/L1_DATASHEET.json").unlink()
    doc, _ = rows("--run-root", str(r))
    ic = doc["rows"][0]["ic"]
    assert ic["name"] is None and ic["label_source"] == "path"
    assert ic["label"] == "some_dir_name" and "L1_DATASHEET" in ic["reason"]


def test_a_run_stating_no_name_is_never_folded_into_a_declared_ic(tmp_path):
    r = make_run(tmp_path / "runs" / "unit_one")   # basename LOOKS like the IC
    (r / "phase1/generated_docs/L1_DATASHEET.json").unlink()
    doc, rc = rows("--root", str(tmp_path / "runs"), "--ic", "unit_one")
    assert [x["declared_ic"] for x in doc["rows"] if x["status"] == "NO_RUN"] == ["unit_one"]


# --------------------------------------------------------------------------
# 8. --per-ic select: TWO labelled rows, ordered by a DECLARED sequence
#    (orchestrator ruling, 2026-09-08)

def test_latest_still_means_newest_and_says_what_it_used(tmp_path):
    make_run(tmp_path / "runs" / "old", run_at="2026-01-01T00:00:00+00:00", verdict="FAIL")
    make_run(tmp_path / "runs" / "new", run_at="2026-02-01T00:00:00+00:00", verdict="PASS")
    doc, _ = rows("--root", str(tmp_path / "runs"), "--per-ic", "select")
    latest = [r for r in doc["rows"] if r.get("role", "").startswith("latest")]
    assert len(latest) == 1 and latest[0]["verdict"] == "PASS"
    assert latest[0]["selected_from"]["latest_by"] == "audit run_at"


def test_furthest_is_a_second_labelled_row_not_a_replacement(tmp_path):
    """The failure the ruling names: a newer phase-2 run hiding an older phase-3 one."""
    make_run(tmp_path / "runs" / "old_deep", run_at="2026-01-01T00:00:00+00:00",
             halted_at="phase3", verdict="FAIL")
    make_run(tmp_path / "runs" / "new_shallow", run_at="2026-02-01T00:00:00+00:00",
             halted_at="phase2", verdict="PASS_WITH_WAIVERS")
    doc, _ = rows("--root", str(tmp_path / "runs"), "--per-ic", "select")
    by_role = {r["role"]: r for r in doc["rows"] if r.get("role")}
    assert set(by_role) == {"latest", "furthest"}
    assert by_role["latest"]["run_root"].endswith("new_shallow")
    assert by_role["furthest"]["run_root"].endswith("old_deep")
    assert "phases[] order" in by_role["furthest"]["selected_from"]["furthest_by"] or \
        "phase1_phase2_phase3.yaml" in by_role["furthest"]["selected_from"]["furthest_by"]


def test_one_run_that_is_both_is_ONE_row_saying_so(tmp_path):
    make_run(tmp_path / "runs" / "only", run_at="2026-02-01T00:00:00+00:00",
             halted_at="phase3")
    doc, _ = rows("--root", str(tmp_path / "runs"), "--per-ic", "select")
    roles = [r["role"] for r in doc["rows"] if r.get("role")]
    assert roles == ["latest+furthest"], "two identical rows would hide the fact"


def test_the_order_is_READ_not_spelled_in_this_program(tmp_path):
    """No ordering literal may live in the source: the flow moves, this follows."""
    body = PROG.read_text().split('"""', 2)[2]
    for banned in ('"phase1", "phase2"', "'phase1', 'phase2'", '"phase2", "phase3"'):
        assert banned not in body, f"an ordering literal {banned!r} is in the body"
    doc, _ = rows("--run-root", str(make_run(tmp_path / "r", halted_at="phase1")))
    entry = doc["declared_order"]["rank"]["phase1"]
    assert "phase1_phase2_phase3.yaml" in entry["by"], (
        "phase1 is declared by the flow file and must be placed from it")


def test_a_halted_at_no_declared_source_places_is_NOT_COMPARABLE(tmp_path):
    make_run(tmp_path / "runs" / "a", halted_at="a_step_no_flow_declares",
             phases=[], run_at="2026-01-01T00:00:00+00:00")
    doc, _ = rows("--root", str(tmp_path / "runs"), "--per-ic", "select")
    resolved = [r for r in doc["rows"] if r["status"] == "RESOLVED"]
    assert len(resolved) == 1 and resolved[0]["role"] == "latest"
    assert "NOT_COMPARABLE" in resolved[0]["selected_from"]["furthest"], (
        "an unplaceable run must not sort last quietly")


def test_a_tie_at_the_furthest_position_says_the_tie_was_broken(tmp_path):
    make_run(tmp_path / "runs" / "a", halted_at="phase3", run_at="2026-01-01T00:00:00+00:00")
    make_run(tmp_path / "runs" / "b", halted_at="phase3", run_at="2026-03-01T00:00:00+00:00")
    doc, _ = rows("--root", str(tmp_path / "runs"), "--per-ic", "select")
    f = [r for r in doc["rows"] if r.get("role", "").endswith("furthest")][0]
    assert "TIE BROKEN BY newest run_at" in f["selected_from"]["furthest_by"]
    assert f["run_root"].endswith("b")


def test_when_every_run_is_undated_none_is_called_the_latest(tmp_path):
    make_run(tmp_path / "runs" / "a", bad_audit=True, verdict="FAIL")
    make_run(tmp_path / "runs" / "b", bad_audit=True, verdict="PASS")
    doc, _ = rows("--root", str(tmp_path / "runs"), "--per-ic", "select")
    latest = [r for r in doc["rows"] if r.get("role", "").startswith("latest")]
    assert len(latest) == 2, "both kept; the selection refuses to guess"
    assert all("NOT_MEASURED" in r["selected_from"]["latest_by"] for r in latest)


# --------------------------------------------------------------------------
# 8b. HOST POPULATION on the face of the report (orchestrator ruling)

def test_a_declared_host_with_no_roots_is_not_measured_and_withholds_the_verdict(tmp_path):
    make_run(tmp_path / "runs" / "a", ic_name="unit_one")
    p = run("--root", str(tmp_path / "runs"), "--declared-host", "host_a",
            "--declared-host", "host_b")
    doc, rc = rows("--root", str(tmp_path / "runs"), "--declared-host", "host_a",
                   "--declared-host", "host_b")
    assert doc["hosts"]["complete"] is False
    missing = [m["host"] for m in doc["hosts"]["not_measured"]]
    assert "host_a" in missing and "host_b" in missing
    assert "VERDICT WITHHELD" in p.stdout and "--merge-json" in p.stdout
    assert rc == 2, "a partial sweep is UNDETERMINED, never a pass"


def test_the_hosts_swept_are_on_the_first_line_not_in_a_footer(tmp_path):
    make_run(tmp_path / "runs" / "a")
    text = run("--root", str(tmp_path / "runs")).stdout
    assert text.splitlines()[0].startswith("HOSTS SWEPT:")


def test_a_sweep_covering_every_declared_host_does_not_withhold(tmp_path):
    make_run(tmp_path / "runs" / "a")
    import socket
    doc, _ = rows("--root", str(tmp_path / "runs"),
                  "--declared-host", socket.gethostname())
    assert doc["hosts"]["complete"] is True


def test_another_hosts_json_can_be_merged_and_keeps_its_host(tmp_path):
    """The five-host answer is assembled WITHOUT running anywhere but here."""
    make_run(tmp_path / "runs" / "a", ic_name="unit_one")
    doc, _ = rows("--root", str(tmp_path / "runs"))
    far = json.loads(json.dumps(doc))          # as that host would have written it
    for r in far["rows"]:
        r["host"] = "host_far"
    side = tmp_path / "other.json"
    side.write_text(json.dumps(far))
    doc2, _ = rows("--root", str(tmp_path / "runs"),
                   "--merge-json", f"host_far:{side}",
                   "--declared-host", "host_far")
    hosts = {r.get("host") for r in doc2["rows"] if r["status"] == "RESOLVED"}
    assert "host_far" in hosts
    assert [m["host"] for m in doc2["hosts"]["not_measured"]] == []


def test_a_merged_rows_own_host_wins_over_the_label_the_caller_typed(tmp_path):
    """Same rule as prose vs the run: the measurement wins, the label is reported."""
    make_run(tmp_path / "runs" / "a")
    doc, _ = rows("--root", str(tmp_path / "runs"))
    side = tmp_path / "other.json"
    side.write_text(json.dumps(doc))               # rows carry THIS host
    doc2, _ = rows("--root", str(tmp_path / "runs"), "--merge-json", f"host_far:{side}")
    merged = [r for r in doc2["rows"] if r.get("merged_from")]
    assert merged and all(r["host"] != "host_far" for r in merged)
    assert all("host_label_disagreement" in r for r in merged)


def test_a_root_with_no_host_half_is_refused(tmp_path):
    p = run("--host-root", str(tmp_path))
    assert p.returncode != 0
    assert "must be HOST:" in (p.stdout + p.stderr)


# --------------------------------------------------------------------------
# 9. what counts as a PROSE CLAIM — measured, not assumed
#
# Reading the first verdict word anywhere in the document charged 429 of 3053
# run roots on one host with a disagreement, and the first inspected were
# per-phase table cells. Anchoring the declaring word anywhere on the line still
# charged 150, on the strength of a quoted artefact record. Both directions are
# pinned here so neither loosening can come back unnoticed.

def test_a_per_phase_table_cell_is_not_the_documents_claim(tmp_path):
    make_run(tmp_path / "r", verdict="FAIL", prose=(
        "# report\n\n| step | result |\n|---|---|\n"
        "| **Phase 1** | PASS | 14/14 docs |\n"))
    doc, rc = rows("--run-root", str(tmp_path / "r"))
    row = doc["rows"][0]
    assert row["disagreements"] == []
    assert row["prose"][0]["claim"] is None
    assert "declares an overall verdict" in row["prose"][0]["claim_reason"]
    assert "PASS" in row["prose"][0]["claims_seen"], "the word was SEEN, and not counted"
    assert rc == 0


def test_a_quoted_artefact_record_is_not_the_documents_claim(tmp_path):
    """A sentence ABOUT an artefact is not a claim about the run."""
    make_run(tmp_path / "r", verdict="FAIL", prose=(
        "# report\n\nthe container record read "
        "`image_match: true`, `verdict: PASS`\n"))
    doc, rc = rows("--run-root", str(tmp_path / "r"))
    assert doc["rows"][0]["disagreements"] == []
    assert rc == 0


@pytest.mark.parametrize("line", [
    "**OVERALL: PRODUCTION-READY** (all pillars pass).",
    "## Overall verdict: PASS",
    "Final status: PASS_WITH_WAIVERS",
])
def test_a_declared_overall_verdict_is_the_documents_claim(tmp_path, line):
    make_run(tmp_path / "r", verdict="FAIL", prose=f"# report\n\n{line}\n")
    doc, rc = rows("--run-root", str(tmp_path / "r"))
    row = doc["rows"][0]
    assert row["disagreements"], f"{line!r} declares a verdict and must be read"
    assert row["prose"][0]["claim_line"].startswith(line[:12])
    assert rc == 1


# --------------------------------------------------------------------------
# 10. what stands BEHIND a table cell (orchestrator ruling, 2026-09-08)
#
# "A markdown table cell is a RENDERING of a verdict, not the verdict. Read the
# artefact the table was generated from, and if that artefact does not exist the
# answer is NOT_MEASURED — never PASS."
#
# The absent-artefact arm is the load-bearing one: it is the only thing standing
# between "I could not read it" and "I read it and it said pass", and a mutation
# that made an absent backing read as PASS passed every other test in this file.

def test_a_table_cell_with_no_backing_artefact_is_not_measured_never_pass(tmp_path):
    make_run(tmp_path / "r", verdict="FAIL", prose=(
        "# report\n\n| step | result |\n|---|---|\n| **Phase 3** | PASS | clean |\n"))
    doc, _ = rows("--run-root", str(tmp_path / "r"))
    pr = doc["rows"][0]["prose"][0]
    assert pr["claim"] is None
    assert pr["table_cells"] == ["PASS"], "the cell is RECORDED"
    assert pr["backing"]["status"] == "NOT_MEASURED"
    assert "cites no artefact" in pr["backing"]["reason"]
    assert "PASS" not in json.dumps(pr["backing"]).replace('"PASS"', "", 0) or \
        pr["backing"].get("verdict") is None, "no verdict may be invented for it"
    text = run("--run-root", str(tmp_path / "r")).stdout
    assert "backing artefact is NOT_MEASURED" in text


def test_a_table_cell_whose_cited_artefact_is_unreadable_is_not_measured(tmp_path):
    make_run(tmp_path / "r", verdict="FAIL", prose=(
        "# report\n\n| step | result |\n|---|---|\n| **Phase 3** | PASS | clean |\n\n"
        "_generated from `reports/audit/not_here.json`._\n"))
    doc, _ = rows("--run-root", str(tmp_path / "r"))
    b = doc["rows"][0]["prose"][0]["backing"]
    assert b["status"] == "NOT_MEASURED"
    assert b["artefacts"][0]["path"] == "reports/audit/not_here.json"
    assert "absent" in b["artefacts"][0]["reason"]


def test_a_table_cell_IS_resolved_to_the_artefact_the_document_cites(tmp_path):
    """The quiet direction: when the artefact is there, it is read and reported —
    and it is the artefact's verdict that appears, not the cell's."""
    make_run(tmp_path / "r", verdict="FAIL", audit_verdict="FAIL", prose=(
        "# report\n\n| step | result |\n|---|---|\n| **Phase 3** | PASS | clean |\n\n"
        "_this table consumes `reports/audit/phase23_completion_audit.json`._\n"))
    doc, _ = rows("--run-root", str(tmp_path / "r"))
    b = doc["rows"][0]["prose"][0]["backing"]
    assert b["status"] == "READ"
    assert b["artefacts"][0]["verdict"] == "FAIL", "the artefact, not the cell"
    assert doc["rows"][0]["disagreements"] == [], "a cell is still not a claim"
