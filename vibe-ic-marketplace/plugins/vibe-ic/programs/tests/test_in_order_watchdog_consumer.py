"""Neutral executable controls through the actual canonical producer and runner."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hostpaths import require_repo

POLICIES = {"counter_bits", "max_accepted", "start", "response_order",
            "timeout_compare", "initial_age", "saturation", "reset", "flag",
            "zero_latency", "violation_policy", "request_protocol"}
ROLES = ["clock", "reset", "request_valid", "request_ready", "response_done",
         "threshold", "timed_out", "violation"]


def _project(root, **changes):
    c = {"schema": "vibeic.in_order_watchdog.v1", "module": "neutral_monitor",
         "ports": dict(zip(ROLES, ["tick", "clear", "offer", "take", "finish",
                                   "limit", "alarm", "fault"])),
         "counter_bits": 3, "max_accepted": 3, "start": "accepted",
         "response_order": "in_order", "timeout_compare": "next_age_ge",
         "initial_age": 0, "saturation": "max",
         "reset": {"polarity": "high", "synchrony": "sync"},
         "flag": "sticky_until_reset", "zero_latency": "complete_on_accept",
         "request_protocol": "hold_valid_until_accepted",
         "violation_policy": "sticky_until_reset"}
    c.update(changes)
    quotes = {k: k + ": " + json.dumps(c[k], sort_keys=True)
              for k in POLICIES | {"module", "ports"}}
    text = "Public declared watchdog interface and behavior.\n" + "\n".join(quotes.values()) + "\n"
    src = root / "input" / "design.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text)
    c["mapping"] = {"actor": "AI", "source": "input/design.md",
                    "sha256": hashlib.sha256(src.read_bytes()).hexdigest(), "citations": quotes}
    declaration = root / "input" / "in_order_watchdog.json"
    declaration.write_text(json.dumps(c))
    return c


def _frontdoor(project):
    program = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic", "programs",
                           "canonical_primitive_synth.py")
    out = subprocess.run([sys.executable, str(program), str(project), "--emit"],
                         capture_output=True, text=True)
    return out.returncode, json.loads(out.stdout.strip().splitlines()[-1])


def _emit(project):
    rc, outcome = _frontdoor(project)
    # Existing consumer's observed DEFER is the substantive pre-fix control,
    # not an import of a new utility or a missing-field assertion.
    assert outcome["verdict"] == "EMIT", outcome
    assert rc == 0
    return Path(outcome["written"])


def _simulate(project, c, rtl_path, steps, tail="", variant="fixed"):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("NOT_MEASURED: host RTL simulator unavailable")
    p = c["ports"]
    aliases = dict(zip(ROLES, ["clk", "rst", "valid", "ready", "done", "threshold",
                             "hit", "bad"]))
    connect = ", ".join("." + p[role] + "(" + aliases[role] + ")" for role in ROLES)
    body = rtl_path.read_text()
    count = re.search(r"reg \[\d+:0\] (wd_*count);", body).group(1)
    ages = re.search(r"reg \[\d+:0\] (wd_*ages) \[", body).group(1)
    active = int(c["reset"]["polarity"] == "high")
    bits = c["counter_bits"]
    calls = "\n".join(f"cycle({int(v)},{int(r)},{int(d)},{bits}'d{t});" for v,r,d,t in steps)
    tb = f"""
module tb;
reg clk=0, rst={active}, valid=0, ready=0, done=0;
reg [{bits-1}:0] threshold=0;
wire hit,bad;
{c['module']} dut({connect});
always #5 clk=~clk;
task cycle;
 input v,r,d; input [{bits-1}:0] t;
 begin
  @(negedge clk); valid=v; ready=r; done=d; threshold=t;
  @(posedge clk); #1;
  $display("sample %0d %0d %0d %0d",hit,bad,dut.{count},dut.{ages}[0]);
 end
endtask
initial begin
 repeat(2) @(posedge clk); #1; rst={1-active};
 {calls}
 {tail}
 $finish;
end
endmodule
"""
    tb_path = project / (variant + "_tb.sv")
    tb_path.write_text(tb)
    exe = project / (variant + ".out")
    built = subprocess.run(["iverilog", "-g2012", "-s", "tb", "-o", str(exe),
                            str(rtl_path), str(tb_path)], capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run(["vvp", str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    return [tuple(map(int, line.split()[1:])) for line in ran.stdout.splitlines()
            if line.startswith("sample ")]


def test_first_response_preserves_the_second_watchdog_and_sticky_flag(tmp_path):
    c = _project(tmp_path)
    rtl = _emit(tmp_path)
    steps = [(1,1,0,4),(1,1,0,4),(0,0,1,4),(0,0,0,4),
             (0,0,0,4),(0,0,0,4),(0,0,1,4),(0,0,0,4)]
    observed = _simulate(tmp_path,c,rtl,steps)
    assert observed == [(0,0,1,0),(0,0,2,1),(0,0,1,1),(0,0,1,2),
                        (0,0,1,3),(1,0,1,4),(1,0,0,0),(1,0,0,0)]
    # Mechanism knockout: completion resetting remaining ages is the stale
    # single-timer lesson. SAME stimulus still discriminates the emitted RTL.
    body = rtl.read_text()
    mutated = re.sub(r"(if \(wd_retire\) wd_ages\[wd_j\] <= )[^;]+;",
                     r"\g<1>0;", body, count=1)
    assert mutated != body
    mutant = tmp_path / "completion_clears_remaining.v"
    mutant.write_text(mutated)
    wrong = _simulate(tmp_path,c,mutant,steps,variant="mutant")
    assert (wrong[5][0],observed[5][0]) == (0,1)


@pytest.mark.parametrize("start,counts", [
    ("first_valid", [1,1,1,1,2,3]), ("accepted", [0,0,0,1,2,3])])
def test_stalled_valid_is_once_but_back_to_back_accepts_are_distinct(tmp_path,start,counts):
    c = _project(tmp_path,start=start)
    rtl = _emit(tmp_path)
    trace = _simulate(tmp_path,c,rtl,[(1,0,0,7)]*3+[(1,1,0,7)]*3)
    assert [r[2] for r in trace] == counts
    assert all(r[1] == 0 for r in trace)


def test_simultaneous_pop_push_preserves_order_and_remaining_age(tmp_path):
    c = _project(tmp_path)
    rtl = _emit(tmp_path)
    trace = _simulate(tmp_path,c,rtl,[(1,1,0,7),(1,1,0,7),(1,1,1,7),
                                    (1,1,1,7),(0,0,1,7),(0,0,1,7)])
    assert [(r[2],r[3]) for r in trace] == [(1,0),(2,1),(2,1),(2,1),(1,1),(0,0)]
    assert all(r[:2] == (0,0) for r in trace)


@pytest.mark.parametrize("threshold,expected", [(0,[1,1]),(1,[0,1])])
def test_zero_and_one_threshold_exact_sampling_convention(tmp_path,threshold,expected):
    c = _project(tmp_path)
    trace = _simulate(tmp_path,c,_emit(tmp_path),[(1,1,0,threshold),(0,0,0,threshold)])
    assert [r[0] for r in trace] == expected


def test_runtime_threshold_changes_and_age_saturation_are_not_equality_tests(tmp_path):
    c = _project(tmp_path,counter_bits=2,flag="pulse")
    rtl = _emit(tmp_path)
    trace = _simulate(tmp_path,c,rtl,[(1,1,0,1),(0,0,0,3),(0,0,0,3),
                                    (0,0,0,3),(0,0,0,3),(0,0,0,2),(0,0,1,2),(0,0,0,2)])
    assert [r[0] for r in trace] == [0,0,0,1,1,1,0,0]
    assert [r[3] for r in trace] == [0,1,2,3,3,3,0,0]
    mutant = tmp_path / "wrapping_age.v"
    mutant.write_text(rtl.read_text().replace("(&age) ? age : age +", "age +"))
    wrong = _simulate(tmp_path,c,mutant,[(1,1,0,1),(0,0,0,3),(0,0,0,3),
                                      (0,0,0,3),(0,0,0,3)],variant="wrap")
    assert (wrong[-1][3],trace[4][3]) == (0,3)


@pytest.mark.parametrize("polarity,synchrony", [
    ("high","sync"),("low","sync"),("high","async"),("low","async")])
def test_reset_polarity_synchrony_and_sticky_clear_are_declared(tmp_path,polarity,synchrony):
    c = _project(tmp_path,reset={"polarity":polarity,"synchrony":synchrony})
    active = int(polarity == "high")
    tail = f"""@(negedge clk); #1; rst={active}; #1;
    $display("sample %0d %0d %0d %0d",hit,bad,dut.wd_count,dut.wd_ages[0]);
    @(posedge clk); #1;
    $display("sample %0d %0d %0d %0d",hit,bad,dut.wd_count,dut.wd_ages[0]);"""
    trace = _simulate(tmp_path,c,_emit(tmp_path),[(1,1,0,0),(0,0,1,0),(0,0,0,0)],tail=tail)
    assert [r[0] for r in trace] == [1,1,1,int(synchrony=="sync"),0]
    assert trace[-1][2:] == (0,0)


def test_fifteen_accepted_plus_one_stalled_slot_and_runtime_violation(tmp_path):
    c = _project(tmp_path,max_accepted=15,counter_bits=6,start="first_valid")
    rtl = _emit(tmp_path)
    steps = [(1,1,0,63)]*15+[(1,0,0,63)]*2+[(1,1,1,63),(1,1,1,63)]
    trace = _simulate(tmp_path,c,rtl,steps)
    assert [r[2] for r in trace] == list(range(1,16))+[16,16,15,15]
    assert all(r[:2] == (0,0) for r in trace)
    small = tmp_path / "small"
    c2 = _project(small,max_accepted=1)
    bad = _simulate(small,c2,_emit(small),[(1,1,0,7),(1,1,0,7),(0,0,1,7),(0,0,0,7)])
    assert [r[1] for r in bad] == [0,1,1,1]
    assert [r[2] for r in bad] == [1,1,0,0]


def test_zero_latency_and_orphan_response_are_distinct(tmp_path):
    c = _project(tmp_path,start="first_valid")
    trace = _simulate(tmp_path,c,_emit(tmp_path),[(1,1,1,0),(0,0,0,0),(1,0,1,7)])
    assert trace[0] == (0,0,0,0)
    assert trace[-1][1:] == (1,1,0)


def test_stalled_request_contract_and_accepted_bound_are_checked(tmp_path):
    c = _project(tmp_path,start="first_valid",max_accepted=1)
    trace = _simulate(tmp_path,c,_emit(tmp_path),[(1,0,0,7),(1,1,1,7),(0,0,0,7)])
    assert [r[:3] for r in trace] == [(0,0,1),(0,0,0),(0,0,0)]
    excess = tmp_path / "excess"
    c2 = _project(excess,start="first_valid",max_accepted=1)
    bad = _simulate(excess,c2,_emit(excess),[(1,1,0,7),(1,0,0,7),(1,1,0,7)])
    assert [r[1] for r in bad] == [0,0,1]
    aborted = tmp_path / "aborted"
    c3 = _project(aborted,start="first_valid")
    dropped = _simulate(aborted,c3,_emit(aborted),[(1,0,0,7),(0,0,0,7)])
    assert [r[1] for r in dropped] == [0,1]


def test_renamed_module_ports_and_internal_name_collision_have_same_behavior(tmp_path):
    ports = dict(zip(ROLES, ["wd_count","reset_z","incoming","accepted","returned",
                            "deadline","sticky_event","broken_contract"]))
    c = _project(tmp_path,module="renamed_monitor",ports=ports)
    trace = _simulate(tmp_path,c,_emit(tmp_path),[(1,1,0,1),(0,0,0,1),(0,0,1,1)])
    assert [r[:3] for r in trace] == [(0,0,1),(1,0,1),(1,0,0)]


@pytest.mark.parametrize("field,value", [
    ("max_accepted",0),("max_accepted",-1),("max_accepted",True),("max_accepted",1025),
    ("counter_bits",0),("counter_bits",65),("response_order","out_of_order"),
    ("start","infer_from_names"),("flag","clear_on_idle"),("initial_age",1),
    ("timeout_compare","equal"),("saturation","wrap"),("module","module"),
    ("request_protocol","cancel_when_idle")])
def test_malformed_or_unsupported_contract_refuses_before_writing(tmp_path,field,value):
    _project(tmp_path,**{field:value})
    rc,out = _frontdoor(tmp_path)
    assert out["verdict"] == "REFUSED",out
    assert rc == 2 and "WATCHDOG_CONTRACT_REFUSED" in out["reason"]
    assert not list((tmp_path / "phase2").rglob("*.v"))


@pytest.mark.parametrize("tamper",["source","quote","missing_reset"])
def test_source_mapping_is_not_silently_guessed(tmp_path,tamper):
    c = _project(tmp_path)
    path = tmp_path / "input" / "in_order_watchdog.json"
    if tamper == "source":
        (tmp_path / "input" / "design.md").write_text("different public input")
    elif tamper == "quote":
        c["mapping"]["citations"]["start"] = "not a source sentence"
        path.write_text(json.dumps(c))
    else:
        del c["reset"]
        path.write_text(json.dumps(c))
    rc,out = _frontdoor(tmp_path)
    assert (rc,out["verdict"]) == (2,"REFUSED")
    assert not list((tmp_path / "phase2").rglob("*.v"))


def test_actual_runner_publishes_under_existing_no_clobber_guard(tmp_path,monkeypatch):
    import design_one_shot_runner as runner
    monkeypatch.setattr(runner,"_RTL_SESSION_OWNED",False)
    monkeypatch.setattr(runner,"_RTL_SESSION_PROJECT",None)
    c = _project(tmp_path)
    result = runner._try_canonical_primitive_rtl(tmp_path,0.0)
    assert ("DEFER" if result is None else result.status) == "PASS"
    assert result.extras["shape"] == "in_order_watchdog"
    published = Path(result.output_files[0])
    frozen = published.read_bytes()
    assert runner._try_canonical_primitive_rtl(tmp_path,0.0) is None
    assert published.read_bytes() == frozen
    rc,out = _frontdoor(tmp_path)
    assert (rc,out["verdict"]) == (2,"REFUSED")
    assert published.read_bytes() == frozen
    trace = _simulate(tmp_path,c,published,[(1,1,0,1),(0,0,0,1)])
    assert [r[0] for r in trace] == [0,1]


def test_actual_runner_blocks_bad_mapping_instead_of_falling_through(tmp_path,monkeypatch):
    import design_one_shot_runner as runner
    monkeypatch.setattr(runner,"_RTL_SESSION_OWNED",False)
    monkeypatch.setattr(runner,"_RTL_SESSION_PROJECT",None)
    _project(tmp_path,response_order="out_of_order")
    result = runner._try_canonical_primitive_rtl(tmp_path,0.0)
    assert ("DEFER" if result is None else result.status) == "FAIL"
    assert "WATCHDOG_CONTRACT_REFUSED" in result.detail
    assert not list((tmp_path / "phase2").rglob("*.v"))


def test_real_expert_database_no_longer_overrides_sticky_multi_outstanding_input(tmp_path):
    database = require_repo("vibe-ic-marketplace","plugins","vibe-ic","agents",
                            "ic_expert_db","ic_expert_db.json")
    db = json.loads(database.read_text())
    entries = db["entries"]
    row = next(x for x in entries if x["ic_class"] == "axi-interconnect-timeout")
    lesson = "\n".join(row["lessons"])
    policy = "completion_or_idle" if "clear the timer AND the latched flag on BOTH" in lesson else "source_declared"
    assert policy == "source_declared"
    assert "per-transaction" in lesson and "prompt-sticky" in lesson
    import _lesson_digest
    stage = tmp_path / "stage"
    stage.mkdir()
    assert _lesson_digest.render_ic_expert_db_digest(stage,"AXI interconnect timeout watchdog transaction outstanding") > 0
    consumed = (stage / "ic_expert_db.md").read_text()
    assert "per-transaction" in consumed and "prompt-sticky" in consumed
