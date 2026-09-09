"""A rung's published `peak_rss_kib` must be a PEAK, not a 30-second poll.

WHY THIS FILE EXISTS (vibe-ic#2194, the half c071f6253 could not close).
That commit split the ladder into one process per rung and proved the split,
but landed `Refs #2194, not Closes` because the SAVING the issue reports was
never measured: its own record reads `legs 2-4 NOT_MEASURED — each ran in under
1 s, shorter than the supervisor's sampling interval`.

The reason is the instrument. `leg_peak_rss_kib` reads the stall supervisor's
samples, and the supervisor polls every `DEFAULT_POLL_S` = 30 s. A poll is not a
peak: it reports NOTHING for a rung shorter than its interval, and for a longer
one it reports whatever the process happened to hold when the clock struck.

MEASURED on a 1153-compared-point miter (64-bit LFSR + 16x64 pipeline, sky130A),
per-rung arm, this repo's own instrument beside Yosys's:

    rung                 supervisor poll     Yosys's own peak
    equiv_simple_full      165100 KiB          154.53 MB
    equiv_induct_seq4       64832 KiB          166.80 MB   <- 2.6x UNDERSTATED
    equiv_induct_seq16      53016 KiB           45.55 MB
    equiv_induct_seq64      53412 KiB           46.00 MB

The seq4 row is the defect: a sample taken while that process was small was
published in a field named `peak`, and the ladder's headline `peak_rss_kib`
(165100) was therefore not the maximum either. #2194's acceptance is "the
ladder's peak becomes the MAX of its rungs" — unreadable off an instrument that
can miss a rung's maximum outright.

Yosys states each process's exact `ru_maxrss` in its own closing line, in
evidence every run already keeps, and nothing read it. These tests pin that it
is read, that it never displaces a real reading with a 0, and that NOT_MEASURED
survives when neither instrument has anything to say.
"""
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "lec_run.py"
assert SCRIPT.exists(), f"Script not found: {SCRIPT}"
sys.path.insert(0, str(SCRIPT.parent))
import lec_run  # noqa: E402

# A real closing line, copied from a run of this repo's own LEC.
_END = ("End of script. Logfile hash: 493cd8c6ef, time: 0.15s, "
        "user: 0.09s, system: 0.04s, MEM: 52.50 MB peak\n")


def _log(mb):
    return ("Yosys 0.68+\n"
            f"End of script. Logfile hash: dead, time: 1s, MEM: {mb} MB peak\n")


# ---------------------------------------------------------------------------
# Reading Yosys's own figure
# ---------------------------------------------------------------------------
def test_the_closing_line_is_read_and_converted_to_kib():
    # 52.50 MB is ru_maxrss/1024, so KiB is the figure times 1024.
    assert lec_run.yosys_self_reported_peak_kib(_END) == round(52.50 * 1024)


def test_an_integer_megabyte_figure_is_read_too():
    assert lec_run.yosys_self_reported_peak_kib(_log("46")) == 46 * 1024


def test_a_log_without_the_closing_line_is_NOT_MEASURED_never_zero():
    out = lec_run.yosys_self_reported_peak_kib("Yosys 0.68+\nkilled\n")
    assert out is None, f"absence must be None, not {out!r}"


def test_an_empty_or_missing_log_is_NOT_MEASURED():
    assert lec_run.yosys_self_reported_peak_kib("") is None
    assert lec_run.yosys_self_reported_peak_kib(None) is None


def test_the_LAST_closing_line_wins_so_a_resumed_leg_reports_its_own():
    # A resumed leg's evidence can carry the previous leg's tail; the process
    # that just ended is the one whose closing line comes last.
    both = _log("10") + "resumed\n" + _log("400")
    assert lec_run.yosys_self_reported_peak_kib(both) == 400 * 1024


# ---------------------------------------------------------------------------
# Choosing between the two instruments
# ---------------------------------------------------------------------------
def test_the_larger_of_the_two_lower_bounds_is_published():
    # THE DEFECT ROW, in the units the record uses: a 64832 KiB poll must not be
    # published as the peak of a rung Yosys measured at 166.80 MB.
    peak, src = lec_run.leg_peak_rss(64832, round(166.80 * 1024))
    assert peak == round(166.80 * 1024), (
        "the poll was published over Yosys's own larger, exact figure")
    assert "yosys" in src and "supervisor" in src, src


def test_a_rung_shorter_than_the_poll_still_gets_a_peak():
    # legs 2-4 of c071f6253's own run: the supervisor had nothing at all.
    peak, src = lec_run.leg_peak_rss(None, 46 * 1024)
    assert peak == 46 * 1024
    assert src == "yosys end-of-script"


def test_the_supervisor_still_wins_when_it_saw_more():
    # It watches the whole stamped process tree, Yosys only itself.
    peak, src = lec_run.leg_peak_rss(222940, round(211.43 * 1024))
    assert peak == 222940
    assert src == "supervisor samples+yosys end-of-script"


def test_neither_instrument_is_NOT_MEASURED_and_never_zero():
    peak, src = lec_run.leg_peak_rss(None, None)
    assert peak is None, f"absence must be None, not {peak!r}"
    assert src is None


def test_a_zero_reading_is_not_treated_as_absent():
    # 0 KiB is a measurement. It must not be silently replaced by the other
    # source's number under the guise of "missing".
    peak, src = lec_run.leg_peak_rss(0, None)
    assert peak == 0 and src == "supervisor samples"
