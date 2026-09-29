"""The std-cell capacitance margin must be a limit the CHECKER reads.

MEASURED on the routed spm DIE (lane drvrcpt, 9 sign-off scenes, pinned
OpenSTA 3.1.0 cdd8ae4d66): whenever the design has supply ports to exclude
(every pad-ring top), `_drv_constraints_sdc_block` emitted the margin as

    set_max_capacitance 0.2 [get_pins -hierarchical *]

OpenSTA accepts that line and stores 1219 pin limits (write_sdc echoes them),
but `CheckCapacitances::findLimit` reads only the top-cell ("design"), port and
Liberty limits -- never a pin's. So every internal driver kept its Liberty
limit (buf_1/Z at 0.2336 pF in ff), `repair_design` never targeted 0.2 pF
(hold8/Z, hold11/Z, wire35/Z, fanout52/Z routed at 0.20-0.34 pF), and the DRV
judge read 2619 rows "tool limit 0.2336/0.2321/0.208 exceeds frozen 0.2".
With `[current_design]` the same census reports 0.2 on every std-cell driver.

The tool arm hands the emitted block to OpenSTA and asks whether an internal
driver pin is checked against it. With neither a host `sta` nor the pinned EDA
image it reports NOT_VERIFIED by name; it never passes silently.

chip-AGNOSTIC: sky130A is an open PDK; cells and limits carry no design name.
"""
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase3_one_shot_runner as P3  # noqa: E402
from not_verified_tier import skip_not_verified  # noqa: E402

_SUPPLIES = ("vccd1", "vssd1")
_IMAGE_LIB = "/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"

# u1/X drives u2 and u3: an INTERNAL driver pin whose load is far above a
# 0.001 pF margin and far below its Liberty max_capacitance.
_NETLIST = """module top (input a, input b, input clk, output y, output z,
                        inout vccd1, inout vssd1);
  wire n1;
  sky130_fd_sc_hd__and2_1 u1 (.A(a), .B(b), .X(n1));
  sky130_fd_sc_hd__buf_1  u2 (.A(n1), .X(y));
  sky130_fd_sc_hd__buf_1  u3 (.A(n1), .X(z));
endmodule
"""


def _block(**kw) -> str:
    base = dict(slew_ns=0.75, cap_pf=0.001, note="n", max_fanout=16,
                fanout_note="fn", supply_ports=_SUPPLIES)
    base.update(kw)
    return P3._drv_constraints_sdc_block(**base)


def _cap_lines(text: str):
    return [l for l in text.splitlines() if l.startswith("set_max_capacitance")]


# ── the emitted text ────────────────────────────────────────────────────────

def test_a_padded_top_carries_the_margin_at_design_scope():
    """The margin reaches every internal driver only through the design."""
    lines = _cap_lines(_block())
    assert "set_max_capacitance 0.001 [current_design]" in lines, lines


def test_no_cap_line_takes_the_pin_scope_the_checker_ignores():
    for sup in ((), _SUPPLIES):
        for line in _cap_lines(_block(supply_ports=sup)):
            assert "get_pins" not in line, line


def test_the_signal_port_line_is_still_emitted_for_a_padded_top():
    """Control: the per-port line that already worked is unchanged."""
    assert ("set_max_capacitance 0.001 $_vibeic_drv_signal_ports"
            in _cap_lines(_block()))


def test_no_cap_declared_emits_no_cap_line():
    """Control: no fabricated limit when none was resolved."""
    assert _cap_lines(_block(cap_pf=None)) == []


# ── the checker ─────────────────────────────────────────────────────────────

def _sta_runner():
    """(argv prefix, liberty path, workdir mapper) for a real OpenSTA."""
    host = shutil.which("sta")
    if host and Path(_IMAGE_LIB).is_file():
        return [host, "-no_init", "-exit"], _IMAGE_LIB
    tried = [f"host sta={host or 'absent'}"]
    if shutil.which("docker"):
        import _eda_pin  # noqa: PLC0415
        image = _eda_pin.image_reference()
        held = subprocess.run(["docker", "image", "inspect", image],
                              capture_output=True, text=True, timeout=60)
        if held.returncode == 0:
            return (["docker", "run", "--rm", "--entrypoint", "sta",
                     "--user", f"{os.getuid()}:{os.getgid()}"], _IMAGE_LIB,
                    image)
        tried.append(f"pinned image {image} not held")
    else:
        tried.append("docker absent")
    skip_not_verified(
        "OpenSTA is not reachable on host " + os.uname().nodename + " ("
        + "; ".join(tried) + "), so the emitted margin cannot be handed to "
        "the checker that ignored the pin scope",
        "run on a lane host that holds the pinned vibeic-eda image")


def _check(tmp_path: Path, block: str) -> str:
    """OpenSTA's max-capacitance report for u1/X under `block`."""
    runner = _sta_runner()
    work = tmp_path / uuid.uuid4().hex[:8]
    work.mkdir(parents=True)
    (work / "d.v").write_text(_NETLIST)
    (work / "blk.sdc").write_text(block)
    (work / "t.tcl").write_text(
        f"read_liberty {runner[1]}\n"
        f"read_verilog {work}/d.v\nlink_design top\n"
        "create_clock -name clk -period 10 [get_ports clk]\n"
        f"source {work}/blk.sdc\n"
        "report_check_types -max_capacitance -verbose -digits 4 "
        "-max_count 100 > " + str(work / "cap.rpt") + "\n")
    if len(runner) == 3:
        argv = runner[0] + ["-v", f"{work}:{work}", runner[2],
                            "-no_init", "-exit", str(work / "t.tcl")]
    else:
        argv = runner[0] + [str(work / "t.tcl")]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    report = work / "cap.rpt"
    assert r.returncode == 0 and report.is_file(), (r.stdout, r.stderr)
    return report.read_text()


def _limit_on(report: str, pin: str) -> float:
    lines = report.splitlines()
    for i, line in enumerate(lines):
        if line.split()[:2] == ["Pin", pin]:
            for follow in lines[i + 1:i + 4]:
                parts = follow.split()
                if parts[:2] == ["max", "capacitance"]:
                    return float(parts[2])
    raise AssertionError(f"{pin} absent from the capacitance report:\n{report}")


def test_the_checker_applies_the_emitted_margin_to_an_internal_driver(tmp_path):
    report = _check(tmp_path, _block())
    assert _limit_on(report, "u1/X") == pytest.approx(0.001), report


def test_the_pin_scope_is_the_form_the_checker_ignores(tmp_path):
    """THE OTHER DIRECTION: restore the pin scope and the internal driver is
    back on its Liberty limit, so the assertion above pins a real difference."""
    good = _block()
    old = good.replace("set_max_capacitance 0.001 [current_design]",
                       "set_max_capacitance 0.001 [get_pins -hierarchical *]")
    assert old != good
    report = _check(tmp_path, old)
    assert _limit_on(report, "u1/X") > 0.01, report


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
