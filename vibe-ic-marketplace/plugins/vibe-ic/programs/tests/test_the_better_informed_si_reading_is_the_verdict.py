"""R-0915-66 — step 27 has two readings of the same design, and they disagree.

MEASURED on subservient x gf180mcuD (lane icsub2, r24, main 845ef5247), from
the run's own two reports:

  the MCF ENVELOPE      si_mcf_sta.json          verdict FAIL, mcf_setup -0.2660
  the DELTA-DELAY screen si_crosstalk.json        delta_delay PASS, 0 violations,
                                                 35902 pairs slack-checked,
                                                 1192 conclusively decoupled

The envelope folds every coupling into its victim's grounded cap at the corner's
Miller factor and assumes every aggressor switches inside every victim's window,
because it has no basis to say otherwise. Its own scope note calls it "a
conservative crosstalk-DELAY bound; closing it is not a silicon claim", and it
has read -0.2660 on ten consecutive trees.

The delta-delay screen HAS that basis. Its own header states the three outcomes
it can reach: "FAIL = proven push-negative; PASS = slack basis covers the worst
delta-delay; ADVISORY = no slack basis (cannot prove)". When it reaches one of
the first two it has measured something the envelope could only bound, and it is
the step's verdict; the envelope is then disclosed beside it with its number
intact.

Both directions are pinned, and the two the ruling names explicitly are the ones
that matter most:
  * ADVISORY may not supersede anything — "no slack basis" is not a finding;
  * a delta-delay FAIL must FAIL the step — a proven push-negative is the one
    thing this screen can assert, and it outranks everything;
  * the envelope's number is never deleted, only re-labelled;
  * and a screen that checked NO pairs has said nothing, whatever it reports.
"""
import json
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import si_mcf_repair as S         # noqa: E402

try:
    import si_mcf_verdict_basis as V  # noqa: E402
except ModuleNotFoundError:           # base sources have no reconciler
    V = None


def _step_verdict(envelope, screen):
    """The verdict step 27 self-reports, asked in a way BASE SOURCES answer.

    Where there is no reconciler the envelope IS the step's verdict — which is
    precisely the defect — so the control below fails on a VALUE rather than on
    a missing module. A test that dies on an ImportError proves the module is
    missing and nothing about what the step decides."""
    if V is None:
        return dict(envelope)
    return V.reconcile(envelope, screen)

# r24's real numbers, transcribed.
_ENVELOPE_FAIL = {
    "program": "si_mcf_sta", "verdict": "FAIL",
    "nominal": {"worst_setup_slack_ns": 2.4975},
    "corners": {"setup": {"worst_slack_after_ns": -0.266,
                          "worst_victim": {"net": "net690"}},
                "hold": {"worst_slack_after_ns": 1.6242}},
}


def _screen(verdict, *, checked=35902, violations=0):
    return {"delta_delay": {"verdict": verdict, "violations_count": violations,
                            "max_delta_delay_ns": 2.7471,
                            "pairs_slack_checked": checked,
                            "pairs_decoupled_by_window": 1192,
                            "scope": "Coupling-based delta-delay screen: ..."}}


# ── the reading that decides ──────────────────────────────────────────────

def test_a_genuine_pass_supersedes_the_envelope():
    """r24 exactly: the envelope says FAIL at -0.266 and the screen proves
    PASS over 35902 slack-checked pairs."""
    out = V.reconcile(_ENVELOPE_FAIL, _screen("PASS"))
    assert out["verdict"] == "PASS"
    b = out["verdict_basis"]
    assert b["mode"] == V.MODE_ADVISES
    assert b["verdict_from"] == "coupling_delta_delay_screen"
    assert b["delta_delay"]["pairs_slack_checked"] == 35902


def test_the_envelopes_number_survives_being_superseded():
    """A bound a better measurement supersedes is still worth reporting and is
    never worth hiding."""
    out = V.reconcile(_ENVELOPE_FAIL, _screen("PASS"))
    env = out["verdict_basis"]["envelope"]
    assert env["verdict"] == "FAIL"
    assert env["mcf_setup_ns"] == -0.266
    assert "-0.266 ns" in env["disclosed_as"]
    # and the report's own corner numbers are untouched
    assert out["corners"]["setup"]["worst_slack_after_ns"] == -0.266
    assert out["nominal"]["worst_setup_slack_ns"] == 2.4975


def test_a_delta_delay_FAIL_fails_the_step():
    """THE FIRST NEGATIVE CONTROL THE RULING NAMES. A proven push-negative is
    the one thing this screen can assert; it outranks everything, including an
    envelope that happened to pass."""
    passing_envelope = dict(_ENVELOPE_FAIL, verdict="PASS")
    out = V.reconcile(passing_envelope, _screen("FAIL", violations=3))
    assert out["verdict"] == "FAIL"
    assert out["verdict_basis"]["mode"] == V.MODE_ADVISES
    assert out["verdict_basis"]["envelope"]["verdict"] == "PASS"


def test_an_advisory_screen_supersedes_nothing():
    """THE SECOND NEGATIVE CONTROL. ADVISORY is the screen's own word for "no
    slack basis (cannot prove)", and an unproven reading may not displace a
    bound."""
    out = V.reconcile(_ENVELOPE_FAIL, _screen("ADVISORY"))
    assert out["verdict"] == "FAIL"
    b = out["verdict_basis"]
    assert b["mode"] == V.MODE_DECIDES
    assert b["verdict_from"] == "mcf_envelope"
    assert "cannot prove anything" in b["why"]


def test_a_screen_that_checked_nothing_has_said_nothing():
    """A verdict reached over zero pairs is an empty scan, not a measurement —
    the same distinction this lane has had to make three times now."""
    for checked in (0, None, "many"):
        out = V.reconcile(_ENVELOPE_FAIL, _screen("PASS", checked=checked))
        assert out["verdict"] == "FAIL", checked
        assert out["verdict_basis"]["mode"] == V.MODE_DECIDES
        assert "not a measurement" in out["verdict_basis"]["why"]


def test_no_screen_at_all_leaves_the_envelope_deciding():
    for absent in (None, {}, {"delta_delay": {}}, {"delta_delay": None}):
        out = V.reconcile(_ENVELOPE_FAIL, absent)
        assert out["verdict"] == "FAIL", absent
        assert out["verdict_basis"]["mode"] == V.MODE_DECIDES


def test_a_verdict_outside_the_screens_vocabulary_decides_nothing():
    for word in ("SI_TIMING_AWARE_SCREEN", "OK", "", None, "pass"):
        out = V.reconcile(_ENVELOPE_FAIL, _screen(word))
        assert out["verdict"] == "FAIL", word
        assert out["verdict_basis"]["mode"] == V.MODE_DECIDES


def test_reconcile_never_mutates_its_input():
    src = json.loads(json.dumps(_ENVELOPE_FAIL))
    V.reconcile(src, _screen("PASS"))
    assert src == _ENVELOPE_FAIL


# ── and what it means for the repair pass ─────────────────────────────────

def test_a_superseded_envelope_does_not_buy_a_repair_pass():
    """The ruling's instruction: do not spend the child when the delta-delay
    screen already passes. It falls out of the reading of record — there is
    nothing left to close."""
    out = V.reconcile(_ENVELOPE_FAIL, _screen("PASS"))
    assert S.envelope_is_open(out) is False


def test_an_advisory_screen_still_lets_the_child_try():
    """THE CONTROL FOR THAT: when the screen proved nothing, the envelope is
    still the verdict and the child still tries."""
    out = V.reconcile(_ENVELOPE_FAIL, _screen("ADVISORY"))
    assert S.envelope_is_open(out) is True


def test_a_delta_delay_fail_also_opens_the_repair_pass():
    out = V.reconcile(dict(_ENVELOPE_FAIL, verdict="PASS"),
                      _screen("FAIL", violations=3))
    assert S.envelope_is_open(out) is True


# ── on disk ───────────────────────────────────────────────────────────────

def _proj(tmp_path, envelope, screen):
    p = tmp_path / "proj"
    (p / "reports" / "phase3").mkdir(parents=True)
    (p / "reports/phase3/si_mcf_sta.json").write_text(json.dumps(envelope))
    if screen is not None:
        (p / "reports/phase3/si_crosstalk.json").write_text(json.dumps(screen))
    return p


def test_apply_rewrites_the_envelope_report_in_place(tmp_path):
    p = _proj(tmp_path, _ENVELOPE_FAIL, _screen("PASS"))
    out = V.apply(p)
    assert out["verdict"] == "PASS"
    on_disk = json.loads((p / "reports/phase3/si_mcf_sta.json").read_text())
    assert on_disk["verdict"] == "PASS"
    assert on_disk["verdict_basis"]["envelope"]["mcf_setup_ns"] == -0.266


def test_apply_decides_nothing_when_there_is_no_envelope_report(tmp_path):
    p = tmp_path / "empty"
    (p / "reports" / "phase3").mkdir(parents=True)
    assert V.apply(p) is None


def test_apply_leaves_a_malformed_envelope_alone(tmp_path):
    p = tmp_path / "proj"
    (p / "reports" / "phase3").mkdir(parents=True)
    bad = p / "reports/phase3/si_mcf_sta.json"
    bad.write_text("{not json")
    assert V.apply(p) is None
    assert bad.read_text() == "{not json"


# ── the defect itself, stated so BASE SOURCES can answer ─────────────────

def test_the_step_verdict_follows_the_reading_that_proved_something():
    """THE DECISIVE CONTROL, and it is base-executable. r24's own two reports:
    the envelope bounds the design at FAIL / -0.2660 ns while the delta-delay
    screen PROVES PASS over 35902 slack-checked pairs with 0 violations. The
    step must report what was proved, not what was assumed."""
    assert _ENVELOPE_FAIL["verdict"] == "FAIL"
    dd = _screen("PASS")["delta_delay"]
    assert dd["verdict"] == "PASS" and dd["violations_count"] == 0
    assert dd["pairs_slack_checked"] == 35902
    out = _step_verdict(_ENVELOPE_FAIL, _screen("PASS"))
    assert out["verdict"] == "PASS", (
        "step 27 self-reports the envelope's bound while the delta-delay "
        "screen has proved the design passes over 35902 slack-checked pairs")


def test_an_advisory_screen_leaves_the_step_on_the_envelope():
    """The both-arms control: with nothing proved, the envelope's FAIL is the
    step's verdict on base sources AND on these."""
    out = _step_verdict(_ENVELOPE_FAIL, _screen("ADVISORY"))
    assert out["verdict"] == "FAIL"
