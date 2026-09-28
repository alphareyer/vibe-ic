"""A7 post-layout re-simulation: bounded, and sized to what it measures (T130).

MEASURED (lane mig109, 8hd-3, delta_sigma on ihp-sg13g2, vibeic-eda 0.3.79):
the A7 tool arm ran ngspice for 32.5 h and was still inside the PRE-layout
transient, five post-layout styles queued behind it, when it was stopped by
PID. Two defects, both in `analog_a7_post_layout_emit`:

  * the deck ran `tran 5n 28673000n` -- 28,673 clocks -- while every windowed
    `meas` card it carries reads `from=523240n to=1025000n` (~500 clocks), so
    the simulation ran ~28x past the last point anything reads;
  * every simulation was launched with `run_to_completion=True`, i.e.
    `timeout -k 5 0`, which GNU `timeout` reads as NO deadline.

The rules the producer follows now, each pinned here against the shipped
`run()` with a stub `docker` that only plays the TOOLS:

  1. the transient stops at the end of the last measurement window plus one
     sample clock, identically in the pre deck and every post deck;
  2. every simulation carries a positive deadline -- the same one for pre and
     post -- from the block's `spec.json` when it states one, else derived
     from the clock count; `deadline_s=0` is refused;
  3. a simulation that spends its budget is NOT_MEASURED (exit 75, reason
     `budget_exhausted`) with the time reached / requested, the wall and the
     remedy -- never a FAIL, never a hang;
  4. a card that genuinely needs a longer record still gets it, and is named.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path
import _eda_pin as PIN
import analog_a7_post_layout_emit as A7
import analog_real_corner_sweep as ARS
from _stated_eda_image import stated_image, state_the_image  # noqa: E402

IMAGE = stated_image()

#: Stated here rather than read off the module, so the pre-fix tree runs these
#: tests and answers WRONGLY instead of failing on a missing name.
SPEC_BUDGET_KEY = "simulation_budget_s"
EX_BUDGET_EXHAUSTED = 75


@pytest.fixture(autouse=True)
def _stated_identity(monkeypatch):
    state_the_image(monkeypatch)


RCX_RC = """* NGSPICE file created from blk.ext - technology: t
.subckt blk a.n1 b vss a x.t1
X0 a.n1 x.t1 vss vss nfet w=1u l=1u
R0 a a.n1 12.5
R1 x.t1 b 3.0
C0 a.n1 vss 1.2f
C1 b vss 0.4f
.ends
"""
TECH = "tech\n  format 35\n  t\nend\ninclude t-extract\n"
TECH_EXTRACT = "extract\n style ngspice variants (),(lvs),(hrhc)\n cscale 1\nend\n"

#: The delta_sigma deck's shape: a 1 MHz clock, a record 28x longer than the
#: window its `meas` cards read, and whole-transient rail cards.
TB = """* tb
.include blk.sp
v_clk a 0 pulse(0 1.2 1000n 1n 1n 499n 1000n)
X1 a b 0 blk
.options trtol=1
.control
tran 5n 28673000n
meas tran vavg avg v(b) from=523240n to=1025000n
meas tran vmax max v(b) from=523240n to=1025000n
echo "MEAS density=" $&vavg
meas tran railx_max_b max v(b)
meas tran railx_min_b min v(b)
.endc
.end
"""

#: Plays docker. `exec` of ngspice records the container-side `timeout`
#: DURATION and the deck, then either answers (a completed run) or prints
#: ngspice's progress lines and exits 124 (the deadline fired).
STUB = r'''#!/usr/bin/env python3
import json, os, re, subprocess, sys
a = sys.argv[1:]
j = " ".join(a)
if a[0] == "inspect":
    if "Mounts" in j:
        print(json.dumps([{"Type": "bind", "Source": os.environ["STUB_ROOT"],
                           "Destination": os.environ["STUB_ROOT"]}]))
    else:
        print("sha256:aaaa\trepo@" + os.environ["STUB_DIGEST"])
    sys.exit(0)
if a[0] == "run":
    if "--help" in j:
        sys.exit(0)
    if "-c" in a and "Config.load" in a[-1]:
        src = re.search(r"p='([^']+)'", a[-1]).group(1)
        out = re.search(r"out='([^']+)'", a[-1]).group(1)
        cfg = json.load(open(src))
        cfg["MAGIC_TECH"] = os.environ["STUB_TECH"]
        open(out, "w").write(json.dumps(cfg))
        sys.exit(0)
    if "librelane.steps" in a:
        folder = a[a.index("-o") + 1]
        state_in = json.load(open(a[a.index("-i") + 1]))
        os.makedirs(folder, exist_ok=True)
        rcx = os.path.join(folder, "blk.rcx.spice")
        open(rcx, "w").write(open(os.environ["STUB_RCX"]).read())
        state_in["spice_rcx"] = rcx
        open(os.path.join(folder, "state_out.json"), "w").write(
            json.dumps(state_in))
        sys.exit(0)
    sys.exit(1)
if a[0] == "exec":
    cmd = a[-1]
    if "--json-measure" in cmd:
        print("unrecognized option"); sys.exit(0)
    if "command -v ngspice" in cmd:
        print("/foss/tools/bin/ngspice"); sys.exit(0)
    if " -b " in cmd:
        deck = cmd.split()[-2].strip("'")
        dur = a[a.index("timeout") + 3] if "timeout" in a else None
        with open(os.environ["STUB_EXEC_LOG"], "a") as fh:
            fh.write(json.dumps({"deck": deck, "timeout": dur}) + "\n")
        mode = os.environ.get("STUB_EXPIRE")
        base = os.path.basename(deck)
        if mode and (mode == "all" or (mode == "post" and "_post_" in base)
                     or (mode not in ("all", "post") and mode in base)):
            sys.stdout.write("Reference value :  1.00000e-06\r"
                             "Reference value :  4.13724e-06\r")
            sys.exit(124)
        post = "_post_" in os.path.basename(deck)
        print("MEAS density= " + ((os.environ.get("STUB_POST_DENSITY")
                                   or "0.59") if post else "0.60"))
        sys.exit(0)
    sys.exit(subprocess.run(["bash", "-c", cmd]).returncode)
sys.exit(0)
'''


def _project(tmp_path: Path, tb: str = TB, spec: dict = None) -> Path:
    project = tmp_path / "proj"
    b = project / "phase3" / "analog" / "blk"
    b.mkdir(parents=True)
    tech_dir = tmp_path / "pdkroot" / "tpdk" / "libs.tech" / "magic"
    tech_dir.mkdir(parents=True)
    (tech_dir / "t.tech").write_text(TECH)
    (tech_dir / "t-extract.tech").write_text(TECH_EXTRACT)
    (b / "blk.gds").write_bytes(b"\x00\x06\x00\x02\x02\x58")
    (b / "layout_provenance.json").write_text(json.dumps(
        {"pdk_sources": {"magic_tech": str(tech_dir / "t.tech")}}))
    (b / "blk.sp").write_text(
        ".lib ../../../models/m.lib tt\n.subckt blk a b vss\n"
        "X0 a b vss vss nfet w=1u l=1u\n.ends blk\n")
    (b / "tb_blk.sp").write_text(tb)
    if spec is not None:
        (b / "spec.json").write_text(json.dumps(spec))
    return project


@pytest.fixture
def stub(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    (d / "docker").write_text(STUB)
    (d / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_ROOT", str(tmp_path))
    monkeypatch.setenv("STUB_DIGEST", PIN.IMAGE_DIGEST)
    monkeypatch.setenv("STUB_TECH",
                       str(tmp_path / "pdkroot/tpdk/libs.tech/magic/t.tech"))
    monkeypatch.setenv("STUB_EXEC_LOG", str(tmp_path / "exec.jsonl"))
    monkeypatch.delenv("STUB_EXPIRE", raising=False)
    monkeypatch.delenv("STUB_POST_DENSITY", raising=False)
    monkeypatch.setenv("VIBEIC_DESIGNS_HOST_ROOT", str(tmp_path))
    rcx = tmp_path / "rcx.spice"
    rcx.write_text(RCX_RC)
    monkeypatch.setenv("STUB_RCX", str(rcx))
    ARS._NGSPICE_CACHE.clear()
    ARS._JSON_MEASURE_SUPPORT.clear()
    ARS._CONTAINER_PATH_CACHE.clear()
    return tmp_path


def _sims(tmp_path: Path) -> list:
    log = tmp_path / "exec.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines()] \
        if log.is_file() else []


def _tran(deck: str) -> list:
    return [ln.strip() for ln in Path(deck).read_text().splitlines()
            if ln.strip().lower().startswith("tran ")]


def _record(project: Path) -> dict:
    return json.loads(
        (project / "phase3/analog/blk/a7_post_layout.json").read_text())


# ── 1. the stop time follows the last measurement window ──────────────────
def test_every_a7_deck_stops_at_the_last_meas_window_plus_one_clock(stub):
    project = _project(stub)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == 0
    sims = _sims(stub)
    assert len(sims) == 3                        # pre + two extraction styles
    # 1025000n (the last `to=`) + one 1000n period of the deck's own clock.
    assert [_tran(s["deck"]) for s in sims] == [["tran 5n 1026000n"]] * 3
    span = _record(project)["transient_span"]
    assert span["rule"] == "last_measurement_window_plus_settle"
    assert span["declared_stop_s"] == pytest.approx(28673e-6)
    assert span["stop_s"] == pytest.approx(1026e-6)
    # the rail cards follow the span; they do not stretch it
    assert {"railx_max_b", "railx_min_b"} <= set(span["span_following"])


def test_the_delivered_a3_testbench_keeps_its_full_record(stub):
    """The bound is A7's copy only: A4 reads the delivered deck's record."""
    project = _project(stub)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == 0
    assert (project / "phase3/analog/blk/tb_blk.sp").read_text() == TB


# ── 2. A7 is supervised without a wall-clock kill ─────────────────────────
def test_every_a7_simulation_uses_the_supervised_no_clock_route(stub):
    project = _project(stub)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == 0
    durations = [s["timeout"] for s in _sims(stub)]
    assert len(durations) == 3
    assert durations == ["0"] * 3, durations
    budget = _record(project)["budget"]
    assert budget["source"] == "default_per_clock"
    expected = (getattr(A7, "BUDGET_FLOOR_S", 0)
                + 1026 * getattr(A7, "BUDGET_S_PER_CLOCK", 0))
    assert budget["seconds"] == pytest.approx(expected, abs=1)


def test_a_budget_the_block_states_is_recorded_not_a_kill_clock(stub):
    project = _project(stub, spec={"block": "blk", "specs": [],
                                   SPEC_BUDGET_KEY: 4321})
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == 0
    assert [s["timeout"] for s in _sims(stub)] == ["0"] * 3
    assert _record(project)["budget"]["source"] == \
        f"spec.json:{SPEC_BUDGET_KEY}"


@pytest.mark.parametrize("bad", [0, 0.0, -5])
def test_timeout_0_is_refused_at_the_launcher(bad):
    with pytest.raises(ValueError, match="deadline"):
        ARS._run_ngspice("no-such-container", "/x.sp", deadline_s=bad)


def test_a_declared_budget_cannot_also_ask_to_run_to_completion():
    with pytest.raises(ValueError, match="opposite"):
        ARS._run_ngspice("no-such-container", "/x.sp", deadline_s=10,
                         run_to_completion=True)


# ── 3. a spent budget is NOT_MEASURED, with its reason ────────────────────
def test_a_pre_run_that_spends_its_budget_is_not_measured_not_failed(
        stub, monkeypatch):
    monkeypatch.setenv("STUB_EXPIRE", "all")
    project = _project(stub)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == \
        EX_BUDGET_EXHAUSTED
    rec = _record(project)
    assert (rec["result"], rec["reason_class"], rec["rule"]) == (
        "NOT_MEASURED", "budget_exhausted", "A7_SIM_BUDGET_EXHAUSTED")
    why = rec["budget_exhausted"]
    assert why["simulated_time_reached_s"] == pytest.approx(4.13724e-06)
    assert why["simulated_time_requested_s"] == pytest.approx(1026e-6)
    assert why["wall_s"] >= 0 and why["budget_s"] > 0
    assert why["budget_source"] == "default_per_clock"
    assert "phase3/analog/simulation_budgets.json" in why["remedy"]
    assert why["deck"].endswith("tb_blk_pre.sp")
    assert not (project / "phase3/analog/blk/pre_vs_post.json").exists()


def test_a_post_run_that_spends_its_budget_keeps_the_pre_result(
        stub, monkeypatch):
    monkeypatch.setenv("STUB_EXPIRE", "post")
    project = _project(stub)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == \
        EX_BUDGET_EXHAUSTED
    rec = _record(project)
    assert rec["result"] == "NOT_MEASURED"
    assert rec["pre"]["measurements"] == {"density": 0.60}
    assert rec["budget_exhausted"]["deck"].endswith("tb_blk_post_ngspice.sp")


def test_the_runner_reports_a_spent_budget_as_not_measured(
        tmp_path, monkeypatch):
    import analog_one_shot_runner as R
    project = _project(tmp_path)
    (project / "phase3/analog/analog_block_list.json").write_text(
        json.dumps({"blocks": [{"name": "blk", "type": "ldo"}]}))
    (project / "phase3/librelane_switch.json").write_text(
        json.dumps({"steps": {"A7": "librelane"}}))
    real_run = R._pr.run

    class _CP:
        returncode = 75
        stdout = ""
        stderr = ("NOT_MEASURED: analog_a7_post_layout_emit "
                  "A7_SIM_BUDGET_EXHAUSTED: tb_blk_pre.sp: simulated 4e-06 s")

    def fake_run(cmd, *a, **k):
        if any(str(x).endswith("analog_a7_post_layout_emit.py") for x in cmd):
            return _CP()
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(R._pr, "run", fake_run)
    monkeypatch.setattr(R._pin, "container_image_digest",
                        lambda c: (PIN.IMAGE_DIGEST, ""))
    res = R.step_for_block(project, {"name": "blk", "type": "ldo"},
                           "A7_post_layout_resim", None)
    assert res.status == "NOT_MEASURED"
    assert str(getattr(res.reason_class, "value", res.reason_class)) == \
        "budget_exhausted"
    assert "A7_SIM_BUDGET_EXHAUSTED" in res.detail


# ── 4. a card that needs a longer record still gets it, and is named ──────
def test_a_later_window_sets_the_stop_not_the_earliest_one(stub):
    tb = TB.replace("meas tran vmax max v(b) from=523240n to=1025000n",
                    "meas tran vmax max v(b) from=523240n to=5000000n")
    project = _project(stub, tb=tb)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == 0
    assert {tuple(_tran(s["deck"])) for s in _sims(stub)} == {
        ("tran 5n 5001000n",)}
    assert _record(project)["transient_span"]["last_window_meas"] == ["vmax"]


@pytest.mark.parametrize("card, holder", [
    ("meas tran tdel trig v(a) val=0.6 rise=1 targ v(b) val=0.6 rise=1",
     "tdel"),
    ("fourier 1meg v(b)", "fourier"),
])
def test_a_card_that_needs_the_declared_record_keeps_it_by_name(
        stub, card, holder):
    tb = TB.replace("meas tran railx_max_b", card + "\nmeas tran railx_max_b")
    project = _project(stub, tb=tb)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == 0
    assert {tuple(_tran(s["deck"])) for s in _sims(stub)} == {
        ("tran 5n 28673000n",)}
    span = _record(project)["transient_span"]
    assert span["rule"] == "a_card_needs_the_declared_record"
    assert [h["card"] for h in span["holds_declared_record"]] == [holder]
    assert span["holds_declared_record"][0]["reason"]


def test_simulated_time_reached_reads_ngspice_progress():
    raw = ("Circuit: tb\nReference value :  4.05468e-06\r"
           "Reference value :  4.13724e-06\rtran simulation interrupted\n")
    assert A7.simulated_time_reached_s(raw) == pytest.approx(4.13724e-06)
    assert A7.simulated_time_reached_s("no progress printed\n") is None


# ── the span reader is a grammar reader (the `_NOT_PROSE` claim) ──────────
_SPAN_SHAPES = (
    "meas tran {t} avg v(b) from=523240n to=1025000n",
    "meas tran k avg v(x1.{t}) from=523240n to=1025000n",
    "meas tran k max v(x1.{t})",
    "meas tran {t} find v(b) at=2000000n",
    "meas tran k trig v(x1.{t}) val=0.6 rise=1 targ v(b) val=0.6 rise=1",
    "meas tran k avg v(b) from=0 to=1025000n\nlet {t} = k / 2",
    "wrdata {t}.txt v(b)",
)


def _denial_vocabulary() -> list:
    """`_prose_polarity`'s OWN denial words, read out of its own patterns."""
    import re
    import _prose_polarity as PP
    raw = PP._DENIAL_CORE + "|" + PP._DENIAL_RETIRED
    out = set()
    for m in re.findall(r"([A-Za-z][A-Za-z' -]{2,})", raw):
        m = m.strip()
        out.add(m[1:] if m.startswith("b") and len(m) > 3 else m)
    return sorted(w for w in out if len(w) >= 2 and not w.startswith("b"))


def _sub(obj, old: str, new: str):
    if isinstance(obj, str):
        return obj.replace(old, new)
    if isinstance(obj, list):
        return [_sub(x, old, new) for x in obj]
    if isinstance(obj, dict):
        return {k: _sub(v, old, new) for k, v in obj.items()}
    return obj


def test_the_not_prose_claim_for_the_span_reader_is_falsifiable():
    """`measurement_span` is in `prose_polarity_consulted_check._NOT_PROSE`.
    THE PROPERTY: a denial word in any name position changes nothing but the
    name. THE CONTRAST: the identical strings, read as prose, are denied."""
    import re
    import _prose_polarity as PP
    tokens = [t for t in _denial_vocabulary()
              if re.fullmatch(r"[A-Za-z_]\w*", t)]
    assert len(tokens) >= 5, "the vocabulary was not read"
    trials = changed = 0
    for tok in tokens:
        for shape in _SPAN_SHAPES:
            trials += 1
            deck = TB.replace("meas tran railx_max_b",
                              shape.format(t=tok) + "\nmeas tran railx_max_b")
            ref = TB.replace("meas tran railx_max_b",
                             shape.format(t="zqz") + "\nmeas tran railx_max_b")
            got = A7.measurement_span(deck)
            want = _sub(A7.measurement_span(ref), "zqz", tok.lower())
            if got != want:
                changed += 1
    assert trials >= 35
    assert changed == 0, ("a denial word changed what the span reader "
                          "derived -- the _NOT_PROSE entry for "
                          "measurement_span is false; delete it, not this "
                          "assertion")
    prose = sum(1 for t in tokens for s in _SPAN_SHAPES
                if PP.is_denied(s.format(t=t)))
    assert prose >= len(tokens), "the vocabulary is not inert as prose"


def test_a_node_spelled_like_a_keyword_is_a_name():
    deck = TB.replace("meas tran railx_max_b",
                      "meas tran railx_max_trig max v(x1.trig)\n"
                      "meas tran railx_max_to max v(x1.to)\n"
                      "meas tran railx_max_b")
    span = A7.measurement_span(deck)
    assert span["stop_s"] == pytest.approx(1026e-6)
    assert {"railx_max_trig", "railx_max_to"} <= set(span["span_following"])


# ── review wave 5 (T130): a card is cut only when it is PROVABLY covered ───
@pytest.mark.parametrize("card, holder", [
    # (a) reads from a point AFTER the cut stop to the end of the run
    ("meas tran vtail avg v(b) from=20000000n", "vtail"),
    # (b) a TRIG AT time is where the search STARTS; the TARG event is later
    ("meas tran tset trig at=1000n targ v(b) val=0.5 rise=2 td=1000n",
     "tset"),
    # (c) a time this reader cannot evaluate
    ("meas tran vend avg v(b) from=0 to={tend}", "vend"),
    # whole-record reductions and analyses
    ("let vmean = mean(v(b))", "vmean"),
    ("fft v(b)", "fft"),
])
def test_a_card_not_provably_covered_keeps_the_declared_stop(
        stub, card, holder):
    tb = TB.replace("meas tran railx_max_b", card + "\nmeas tran railx_max_b")
    project = _project(stub, tb=tb)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == 0
    assert {tuple(_tran(s["deck"])) for s in _sims(stub)} == {
        ("tran 5n 28673000n",)}
    span = _record(project)["transient_span"]
    assert span["rule"] == "a_card_needs_the_declared_record"
    assert holder in [h["card"] for h in span["holds_declared_record"]]


def test_a_window_ends_at_the_latest_time_it_references():
    tb = TB.replace("meas tran railx_max_b",
                    "meas tran vfind find v(b) at=2000000n\n"
                    "meas tran railx_max_b")
    span = A7.measurement_span(tb)
    assert span["stop_s"] == pytest.approx(2001e-6)
    assert span["last_window_meas"] == ["vfind"]


def test_a_transient_card_split_across_lines_is_not_reported_as_cut(stub):
    tb = TB.replace("tran 5n 28673000n", "tran 5n\n+ 28673000n")
    # the reader itself does not claim a cut it cannot make ...
    assert A7.measurement_span(tb)["rule"] == \
        "transient_card_continued_across_lines"
    # ... and the writer, which is the one that would make it, agrees
    out, span = A7.bound_transient(tb)
    assert out == tb
    assert span["stop_s"] == pytest.approx(28673e-6)
    assert span["rule"] == "transient_card_continued_across_lines"


def test_two_transient_cards_keep_the_declared_record_without_a_partial_cut():
    tb = TB.replace("meas tran vavg avg v(b) from=523240n to=1025000n",
                    "meas tran vavg avg v(b) from=523240n to=1025000n\n"
                    "alter vload dc=2\ntran 5n 28673000n\n"
                    "meas tran vload avg v(b) from=523240n to=1025000n")
    out, span = A7.bound_transient(tb)
    assert out == tb
    assert span["rule"] == "more_than_one_transient_card"
    assert span["stop_s"] == pytest.approx(28673e-6)


# ── review wave 5 (T130): one style's spent budget voids only that style ──
def test_a_measured_degradation_is_a_fail_even_if_a_later_style_expires(
        stub, monkeypatch):
    monkeypatch.setenv("STUB_POST_DENSITY", "0.30")   # -50 % post-layout
    monkeypatch.setenv("STUB_EXPIRE", "hrhc")         # second style runs out
    project = _project(stub)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == 1
    rec = _record(project)
    assert rec["rule"] == "A7_POSTSIM_DELTA_TOO_BIG"
    assert rec["exhausted_styles"] == ["ngspice(hrhc)"]
    doc = json.loads((project / "phase3/analog/blk/pre_vs_post.json")
                     .read_text())
    assert [s["name"] for s in doc["specs"]] == ["density@ngspice()"]
    assert "*@ngspice(hrhc)" in doc["_provenance"]["not_compared"]


def test_a_later_style_that_expires_keeps_the_rows_already_measured(
        stub, monkeypatch):
    monkeypatch.setenv("STUB_EXPIRE", "hrhc")
    project = _project(stub)
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == EX_BUDGET_EXHAUSTED
    rec = _record(project)
    assert (rec["result"], rec["reason_class"]) == ("NOT_MEASURED",
                                                    "budget_exhausted")
    assert rec["exhausted_styles"] == ["ngspice(hrhc)"]
    assert rec["budget_exhausted"]["deck"].endswith(
        "tb_blk_post_ngspice_hrhc.sp")
    doc = json.loads((project / "phase3/analog/blk/pre_vs_post.json")
                     .read_text())
    assert [s["name"] for s in doc["specs"]] == ["density@ngspice()"]


# ── review wave 5 (T130): budgets for decks with no clock, and where ──────
def test_a_deck_without_a_clock_is_budgeted_from_its_declared_transient():
    deck = ".tran 10n 2m\nmeas tran v avg v(b) from=0 to=1m\n.end\n"
    secs, src = A7.simulation_budget({}, deck, 2e-3)
    assert secs == pytest.approx(A7.BUDGET_FLOOR_S + 2e6 * 0.5)
    assert src["source"] == "default_per_transient_ns"


def test_an_op_or_ac_deck_gets_the_floor_not_the_old_120_s():
    secs, _src = A7.simulation_budget({}, ".control\nac dec 20 1 1g\n.endc\n",
                                      None)
    assert secs == A7.BUDGET_FLOOR_S >= 600


def test_the_projects_budget_file_wins_and_the_remedy_points_to_it(
        stub, monkeypatch):
    project = _project(stub, spec={SPEC_BUDGET_KEY: 4321})
    (project / "phase3/analog/simulation_budgets.json").write_text(
        json.dumps({"blk": 777}))
    monkeypatch.setenv("STUB_EXPIRE", "all")
    assert A7.run(project, "blk", "vibeic-eda", IMAGE) == EX_BUDGET_EXHAUSTED
    assert [s["timeout"] for s in _sims(stub)] == ["0"]
    rec = _record(project)
    assert rec["budget"]["source"] == \
        "phase3/analog/simulation_budgets.json:blk"
    assert "phase3/analog/simulation_budgets.json" in \
        rec["budget_exhausted"]["remedy"]
