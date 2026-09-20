"""vibe-ic#2112 — a HARDMACRO delivery must ship no die seal ring.

MEASURED, on a real front-door run of `subservient` (lane rbsub4, 2026-09-07,
pinned EDA image 0.3.48, gf180mcuD). The design declared
`deliverable = HARDMACRO` and the flow's own step 0.5ic AGREED, in as many
words: "it is delivered, not fabricated, so it terminates at the hardmacro kit
and needs no pad ring, seal ring or submission check". A later step then
derived the opposite from a TOOL CAPABILITY — "the technology ships a die
seal-ring generator ... a die in a process whose own sign-off tech tree carries
a die-seal generator carries the ring it builds" — published
`seal_ring_required=true` into the declaration, and die finishing stamped a die
seal ring into the SHIPPED GDS.

The ring brought a die-sized guard-ring marker with it, so the sign-off deck
evaluated the whole routed design as guard-ring metal. Measured over the run's
own KLayout RDB, summing <multiplicity> per <category>:

    TOTAL 1359528   GR.4 1299340   GR.2 24652   GR.6 1022
    GR.* family = 1325014 = 97.46%   |   everything else = 34514

The premise of that derivation is "a die". A hardmacro is placed INSIDE
somebody else's die, and that parent die owns the ring and the die-level
markers. `_tapeout_declaration` has said so since it was written — every
question of section 2C is `required_for=(DELIVERABLE_DIE,)` — and until this
fix neither the producer nor the derivation read `deliverable` back.

TWO GUARDS, and both are pinned here:
  * `die_finishing_gen._hardmacro_skip` — the producer. It is what actually
    stops the ring being GENERATED, and it holds even when the declaration
    carries no section-2C answer at all (which is the state the run was in
    before the derivation wrote one).
  * `phase3_one_shot_runner._declared_seal_ring_required` — the derivation,
    refused one step earlier at the point where the wrong premise was taken.

Both directions are proved: declare DIE and the same tree gets past the guard.
"""
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import _tapeout_declaration as TD  # noqa: E402
import _owner_declared as _OD                              # noqa: E402
import die_finishing_gen as G  # noqa: E402


def _declare(project: Path, **answers):
    """Write a tape-out declaration carrying exactly `answers`."""
    path = project / TD.DECLARATION_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        _OD.attest({"schema": TD.SCHEMA, "answers": dict(answers)}),
        indent=2) + "\n")
    return path


def _run(project: Path):
    return G.run(project, gds=None, script=None, form=None, tech=None,
                 pdk_root=None, pdk=None, python="python3", marker=None,
                 width=None, height=None, out=None, in_place=True,
                 report=None)


# --------------------------------------------------------------------------- #
# the predicate
# --------------------------------------------------------------------------- #
def test_hardmacro_earns_a_named_skip():
    got = G._hardmacro_skip({"deliverable": TD.DELIVERABLE_HARDMACRO})
    assert got is not None
    assert got["state"] == "DISCLOSED_SKIP"
    # `marker=True`: considered and legitimately not applicable, so it earns
    # `die_finishing.SKIPPED.txt` — the same class as "this PDK ships no
    # generator", NOT the "could not run" class.
    assert got["marker"] is True
    assert got["skipped_by"] == G.SKIPPED_BY_DELIVERABLE
    assert got["deliverable"] == TD.DELIVERABLE_HARDMACRO
    assert TD.DELIVERABLE_HARDMACRO in got["reason"]


def test_a_die_is_not_skipped():
    """THE OTHER DIRECTION. A guard that returns a skip for everything would
    pass the test above."""
    assert G._hardmacro_skip({"deliverable": TD.DELIVERABLE_DIE}) is None


def test_an_undeclared_delivery_is_not_skipped():
    """`applicable()`'s own rule: a design that has not said what it is owes
    every answer. Silence must not buy the skip a hardmacro earns."""
    assert G._hardmacro_skip({}) is None
    assert G._hardmacro_skip({"deliverable": TD.NOT_DETERMINED}) is None


def test_an_answered_seal_ring_required_is_carried_not_dropped():
    """The measured run's own state: `deliverable=HARDMACRO` AND
    `seal_ring_required=true` in one file. The answer is read and reported —
    it does not silence the skip, and the skip does not silence it."""
    got = G._hardmacro_skip({"deliverable": TD.DELIVERABLE_HARDMACRO,
                             "seal_ring_required": True})
    assert got["seal_ring_required_not_applicable"] is True
    assert "seal_ring_required=True" in got["reason"]
    assert got["marker"] is True


def test_no_seal_ring_required_key_means_no_such_claim():
    got = G._hardmacro_skip({"deliverable": TD.DELIVERABLE_HARDMACRO})
    assert "seal_ring_required_not_applicable" not in got


# --------------------------------------------------------------------------- #
# the producer, end to end — no container, no GDS, no PDK needed: the guard
# returns before any of them is looked for, which is the point.
# --------------------------------------------------------------------------- #
def test_the_run_skips_and_writes_the_skipped_marker(tmp_path):
    _declare(tmp_path, deliverable=TD.DELIVERABLE_HARDMACRO,
             seal_ring_required=True)
    res = _run(tmp_path)
    seal = res["seal_ring"]
    assert seal["state"] == "DISCLOSED_SKIP"
    assert seal["skipped_by"] == G.SKIPPED_BY_DELIVERABLE
    marker = tmp_path / "phase3/stage3/pnr/die_finishing.SKIPPED.txt"
    assert marker.is_file()
    assert not (tmp_path / "phase3/stage3/pnr/die_finished.def").exists()
    written = json.loads((tmp_path / "reports/phase3/die_finishing.json")
                         .read_text())
    assert written["seal_ring"]["skipped_by"] == G.SKIPPED_BY_DELIVERABLE


def test_the_die_arm_of_the_same_tree_gets_past_the_guard(tmp_path):
    """THE CONTROL, and it is the whole proof that the DECLARATION decided it.

    One word changes. If the run declined for an environment reason — no GDS,
    no generator, no KLayout — it would decline identically here, and it does
    NOT: this tree reaches a different refusal, with a different reason.
    """
    _declare(tmp_path, deliverable=TD.DELIVERABLE_DIE, seal_ring_required=True)
    res = _run(tmp_path)
    seal = res["seal_ring"]
    assert seal.get("skipped_by") != G.SKIPPED_BY_DELIVERABLE
    assert TD.DELIVERABLE_HARDMACRO not in seal.get("reason", "")


def test_a_hardmacro_is_not_failed_for_an_unanswered_die_question(tmp_path):
    """ORDERING. Section 2C's "started and abandoned" refusal fires when any
    2C question is answered and `seal_ring_required` is not. That refusal must
    not reach a hardmacro, which owes section 2C nothing — so the delivery
    guard is read FIRST. Its DIE twin below is what proves the refusal is
    still live."""
    _declare(tmp_path, deliverable=TD.DELIVERABLE_HARDMACRO,
             seal_ring_script="libs.tech/klayout/tech/scripts/sealring.py")
    res = _run(tmp_path)
    assert res["seal_ring"]["state"] == "DISCLOSED_SKIP"
    assert res["seal_ring"]["skipped_by"] == G.SKIPPED_BY_DELIVERABLE


def test_the_same_abandoned_declaration_still_fails_a_die(tmp_path):
    _declare(tmp_path, deliverable=TD.DELIVERABLE_DIE,
             seal_ring_script="libs.tech/klayout/tech/scripts/sealring.py")
    res = _run(tmp_path)
    assert res["seal_ring"]["state"] == "FAIL"
    assert "seal_ring_required" in res["seal_ring"]["reason"]


def test_an_unreadable_declaration_still_fails_before_any_delivery_claim(
        tmp_path):
    """"I could not read it" must never become "it said HARDMACRO"."""
    path = tmp_path / TD.DECLARATION_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")
    res = _run(tmp_path)
    assert res["seal_ring"]["state"] == "FAIL"
    assert res["seal_ring"].get("skipped_by") is None


# --------------------------------------------------------------------------- #
# the derivation, in the runner
# --------------------------------------------------------------------------- #
def _runner():
    import importlib
    return importlib.import_module("phase3_one_shot_runner")


def test_the_technology_does_not_answer_for_a_hardmacro(tmp_path):
    R = _runner()
    got, why = R._declared_seal_ring_required(tmp_path, None, "",
                                              TD.DELIVERABLE_HARDMACRO)
    assert got is None
    assert "HARDMACRO" in why
    assert "2112" in why


def test_the_declarations_own_deliverable_outranks_the_derivation(tmp_path):
    R = _runner()
    _declare(tmp_path, deliverable=TD.DELIVERABLE_HARDMACRO)
    assert R._effective_deliverable(tmp_path, TD.DELIVERABLE_DIE) == \
        TD.DELIVERABLE_HARDMACRO


def test_a_silent_declaration_is_NOT_DECLARED_and_no_derivation_replaces_it(
        tmp_path):
    """R-0915-95 (#2369): SILENCE IS NOT AN ANSWER, and no derivation fills it.

    THIS TEST USED TO ASSERT THE OPPOSITE and it is re-pinned, not weakened.
    It read `_effective_deliverable(project, HARDMACRO) == HARDMACRO` over a
    declaration that answers nothing — i.e. the derivation standing in for a
    silent owner. That is the defect the ruling deleted: on 2026-09-06 an agent
    inferred HARDMACRO from five designs' documents being silent about a pad
    ring, every check said PASS, eleven days of lanes ran the IP route, and an
    IP result was published as an IC PASS.

    The rule now has THREE arms and this pins all of them, because the middle
    one is only meaningful beside the other two:

        no declaration FILE at all   -> the derivation still applies
        a declaration that is SILENT -> None, whatever the derivation says
        a declaration that ANSWERS   -> its answer, over a contrary derivation

    The silent arm is asserted against BOTH a HARDMACRO and a DIE derivation:
    `is None` against one derivation alone would also hold for a function that
    simply ignored that particular value.
    """
    R = _runner()

    # No file: the derivation is still what a delivery with nothing declared
    # about it IS. The ruling removed the silent-DECLARATION path, not this one.
    assert R._effective_deliverable(tmp_path, TD.DELIVERABLE_HARDMACRO) == \
        TD.DELIVERABLE_HARDMACRO

    _declare(tmp_path)
    for derived in (TD.DELIVERABLE_HARDMACRO, TD.DELIVERABLE_DIE, None):
        assert R._effective_deliverable(tmp_path, derived) is None, derived


def test_a_silent_declaration_halts_step_0_5_rather_than_routing_on_a_guess(
        tmp_path):
    """The consequence of the arm above, at the place it is enforced.

    `_effective_deliverable` returning None is only half the ruling; the other
    half is that Phase 3 REFUSES rather than proceeding down whichever route a
    derivation would have picked. Asserted on the refusal's own words so this
    cannot pass on an unrelated refusal.
    """
    R = _runner()
    _declare(tmp_path)
    why = R._delivery_admission_refusal(tmp_path)
    assert why, "a silent declaration did not halt the backend"
    assert "no owner-attested DIE or HARDMACRO answer" in why, why
