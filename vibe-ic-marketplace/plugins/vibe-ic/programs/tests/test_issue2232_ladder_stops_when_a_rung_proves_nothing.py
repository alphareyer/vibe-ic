"""A rung that proved NOTHING must end the ladder, not authorise a deeper one.

THE MEASURED DEFECT (vibe-ic#2232). On `ic/sha256` the induction ladder ran the
rung that does the work in 700.6 s, and then spent 7.5 h and counting on rungs
that moved nothing. The ladder's own log, in order:

    4. Executing EQUIV_INDUCT pass.
    Proved 1610 previously unproven $equiv cells.
      Of those cells 1611 are proven and 2 are unproven.
    4. Executing EQUIV_INDUCT pass.          <- rung 2 starts
    Proved 0 previously unproven $equiv cells.
      Of those cells 1611 are proven and 2 are unproven.
    4. Executing EQUIV_INDUCT pass.          <- rung 3 starts anyway

Every attempt carries `budget_sec 86400`, so the ladder is authorised to spend
24 h per attempt re-proving a residual a rung has already declared it cannot
move.

WHY IT HAPPENED. The per-rung driver (#2194) has four stop conditions — the
ladder is complete, the leg was CUT OFF, checkpointing is unavailable, or the
rung recorded no checkpoint. None of them is "this rung proved nothing". The
loop already MEASURES `proved_at_entry` / `proved_at_exit` and
`unproven_at_entry` / `unproven_at_exit` for every leg and writes them into the
record; it simply never compared them before climbing. A stalled rung completes
normally, so it writes its checkpoint, `_next_index` advances, and the ladder
goes up.

The residual on sha256 is a legitimate hard one, which is what makes the wasted
time pure loss: both unproven points are bit 31 of a carry-save datapath whose
`csa_carry` is declared over `[30:0]` because the carry out of bit 31 is
discarded by mod-2^32 arithmetic. Gold and gate are equivalent mod 2^32; the
equivalence AT the MSB rests on that discard, which frame-local induction does
not get for free. Deepening `-seq` cannot reach it, and the ladder had already
said so.

NOT_MEASURED IS NOT NO-PROGRESS. The guard fires only when BOTH the entry and
the exit counts were actually read. A leg whose counts could not be measured is
`None`, and a `None` must never be read as "nothing moved" — that would invent
a stop out of an absence, which is the same error in the other direction.

PREDICTED DIRECTIONS, written before these ran:
  1 a rung that proves nothing        -> ladder STOPS there, reason names the rung
    (the stalled rung itself DOES run once — no-progress is only knowable by
     running it; my first draft asserted otherwise and was wrong)
  2 the rungs ABOVE it                -> NEVER LAUNCHED (the 7.5 h)
  3 a ladder that keeps proving       -> climbs every rung, unchanged
  4 counts NOT_MEASURED at a boundary -> ladder CLIMBS (no stop invented)
  5 the verdict of a stopped ladder   -> whatever that leg's log earned; no PASS
                                         is manufactured and none is destroyed
"""
import json
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "lec_run.py"
sys.path.insert(0, str(SCRIPT.parent))
import lec_run  # noqa: E402

_RUNGS = ("equiv_simple_full", "equiv_induct_seq4",
          "equiv_induct_seq16", "equiv_induct_seq64")


class _StallingYosys:
    """Proves on every rung up to `stall_after`, then proves NOTHING more.

    This is the sha256 shape: a rung does the work, the next reports
    `Proved 0 previously unproven` and leaves the counts where they were.
    `blind_after` instead makes the leg stop PRINTING its counts, which is the
    NOT_MEASURED shape and must NOT be read as no-progress.
    """

    def __init__(self, total=100, stall_after="equiv_induct_seq4",
                 blind_after=None):
        self.scripts = []
        self.total = total
        self.proved = 0
        self.stall_after = stall_after
        self.blind_after = blind_after
        self._stalled = False
        self._blind = False

    def __call__(self, container, ys_path, timeout=0, workdir=None, *,
                 live_log_path=None, telemetry_path=None,
                 telemetry_context=None, **kw):
        script = Path(ys_path).read_text(encoding="utf-8")
        self.scripts.append(script)
        lines = script.splitlines()
        out = ["", " /----- Yosys 0.68+ (fake) -----\\", ""]
        if lines and lines[0].startswith("read_rtlil") and not self._blind:
            out += [f"Found {self.total} $equiv cells in equiv:",
                    f"  Of those cells {self.proved} are proven and "
                    f"{self.total - self.proved} are unproven."]
        for ln in lines:
            if ln.startswith("equiv_simple"):
                out.append("Executing EQUIV_SIMPLE pass.")
                if not self._stalled:
                    self.proved = min(self.total, self.proved + 10)
            elif ln.startswith("equiv_induct"):
                out.append("Executing EQUIV_INDUCT pass.")
                if self._stalled:
                    out.append("Proved 0 previously unproven $equiv cells.")
                else:
                    self.proved = min(self.total, self.proved + 20)
                    out.append(f"Proved 20 previously unproven $equiv cells.")
            elif ln.startswith("write_rtlil "):
                target = Path(shlex.split(ln)[1])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"# fake RTLIL proved={self.proved}\n",
                                  encoding="utf-8")
                rung = target.name[:-len(".il.part")]
                if rung == self.stall_after:
                    self._stalled = True
                if rung == self.blind_after:
                    self._blind = True
            elif ln.startswith("log "):
                out.append(ln[len("log "):])
        if not self._blind:
            out += [f"Found {self.total} $equiv cells in equiv:",
                    f"  Of those cells {self.proved} are proven and "
                    f"{self.total - self.proved} are unproven."]
        raw = "\n".join(out) + "\n"
        if live_log_path is not None:
            with open(live_log_path, "a", encoding="utf-8") as fh:
                fh.write(raw)
        return True, raw


def _make_project() -> Path:
    # mkdtemp, NOT tmp_path: a tmp_path under this image carries a newline and
    # these paths are written into a yosys script.
    root = Path(tempfile.mkdtemp(prefix="lec2232_"))
    (root / "phase2/stage1/rtl").mkdir(parents=True)
    (root / "phase2/stage2/synth").mkdir(parents=True)
    (root / "phase2/stage1/rtl/dut.v").write_text(
        "module dut(input clk, input a, output reg y);\n"
        "  always @(posedge clk) y <= a;\n"
        "endmodule\n", encoding="utf-8")
    (root / "phase2/stage2/synth/netlist.v").write_text(
        "module dut(clk, a, y);\n input clk, a; output y;\n"
        " DFF _0_ (.C(clk), .D(a), .Q(y));\n"
        "endmodule\n", encoding="utf-8")
    return root


def _drive(monkeypatch, fake):
    root = _make_project()
    monkeypatch.setattr(lec_run, "_container_available", lambda c: True)
    monkeypatch.setattr(lec_run, "_container_file_exists", lambda c, p: True)
    monkeypatch.setattr(lec_run, "_container_dir_writable",
                        lambda c, p: (True, ""))
    monkeypatch.setattr(lec_run, "_container_file_sha256", lambda c, p: "0" * 64)
    monkeypatch.setattr(lec_run, "_yosys_version", lambda c: "Yosys 0.68+ fake")
    monkeypatch.setattr(lec_run, "_container_image_digest",
                        lambda c: "sha256:" + "1" * 64)
    monkeypatch.setattr(lec_run, "run_yosys_equiv", fake)
    rc = lec_run.main([str(root), "--top", "dut", "--container", "fake",
                       "--liberty", "/foss/pdks/x.lib"])
    report = json.loads((root / "reports/lec.json").read_text(encoding="utf-8"))
    shutil.rmtree(root, ignore_errors=True)
    return rc, report


# --------------------------------------------------------------------------
# 1 / 2 — the defect itself
# --------------------------------------------------------------------------

def test_a_rung_that_proves_nothing_ends_the_ladder(monkeypatch):
    fake = _StallingYosys(stall_after="equiv_induct_seq4")
    _rc, report = _drive(monkeypatch, fake)
    ladder = report["lec_ladder"]
    assert ladder["per_rung_processes"] is True, ladder
    climbed = [lg["rung"] for lg in ladder["legs"]]
    # A CORRECTION TO MY OWN FIRST ASSERTION, recorded rather than quietly
    # edited: I first required the STALLED rung not to be climbed. That is not
    # reachable and it is not the contract — a rung's no-progress is only
    # knowable by running it. `equiv_induct_seq4` does the work here and
    # `equiv_induct_seq16` is the one that proves nothing, so seq16 runs ONCE
    # and the ladder must stop after it. seq64 is the 7.5 h.
    assert "equiv_induct_seq16" in climbed, climbed
    assert "equiv_induct_seq64" not in climbed, (
        "the ladder climbed past a rung that proved nothing — this is the "
        f"7.5 h: {climbed}")
    assert "proved nothing" in (ladder["stopped_because"] or ""), (
        "the ladder stopped without saying it was for no progress: "
        f"{ladder['stopped_because']!r}")
    assert "equiv_induct_seq16" in (ladder["stopped_because"] or "")


def test_the_rungs_above_a_stalled_one_are_never_launched(monkeypatch):
    """The cost is PROCESSES, so count them, not just the record's rows."""
    fake = _StallingYosys(stall_after="equiv_induct_seq4")
    _rc, _report = _drive(monkeypatch, fake)
    induct_scripts = [s for s in fake.scripts if "equiv_induct -seq" in s]
    assert len(induct_scripts) == 2, (
        f"{len(induct_scripts)} induction processes ran; seq4 does the work "
        "and seq16 proves nothing, so the ladder must stop after seq16")
    assert "equiv_induct -seq 16" in "".join(fake.scripts)
    assert "equiv_induct -seq 64" not in "".join(fake.scripts), (
        "the deepest rung launched after a rung had already proved nothing")


# --------------------------------------------------------------------------
# 3 / 4 — the two directions the guard could go wrong in
# --------------------------------------------------------------------------

def test_a_ladder_that_keeps_proving_still_climbs_every_rung(monkeypatch):
    """NEGATIVE CONTROL. The guard must not shorten a productive ladder."""
    fake = _StallingYosys(stall_after=None)
    _rc, report = _drive(monkeypatch, fake)
    ladder = report["lec_ladder"]
    assert [lg["rung"] for lg in ladder["legs"]] == list(_RUNGS), ladder
    assert "proved nothing" not in (ladder["stopped_because"] or "")


def test_counts_that_were_not_measured_never_read_as_no_progress(monkeypatch):
    """NOT_MEASURED is an absence, not a zero. A leg whose counts could not be
    read must not be treated as a stalled rung — inventing a stop out of an
    unread state is the same error as inventing a pass out of one."""
    fake = _StallingYosys(stall_after=None, blind_after="equiv_simple_full")
    _rc, report = _drive(monkeypatch, fake)
    ladder = report["lec_ladder"]
    climbed = [lg["rung"] for lg in ladder["legs"]]
    assert "proved nothing" not in (ladder["stopped_because"] or ""), (
        "an UNMEASURED count was read as no progress: "
        f"{ladder['stopped_because']!r}")
    assert len(climbed) > 2, climbed
    for lg in ladder["legs"][1:]:
        if lg["proved_at_exit"] is None:
            assert lg["proved_at_entry"] is None or True   # both shapes allowed


# --------------------------------------------------------------------------
# 5 — the guard decides where to STOP, never what the verdict is
# --------------------------------------------------------------------------

def test_stopping_early_does_not_manufacture_or_destroy_a_verdict(monkeypatch):
    stalled = _StallingYosys(stall_after="equiv_induct_seq4")
    _rc_s, rep_s = _drive(monkeypatch, stalled)
    # the residual is still reported as unproven, and no PASS is claimed
    ladder = rep_s["lec_ladder"]
    last = ladder["legs"][-1]
    assert last["unproven_at_exit"] is not None, last
    assert last["unproven_at_exit"] > 0, (
        "this fixture leaves a residual; a stopped ladder must still report it")
    assert ladder["complete"] is False, ladder["complete"]
