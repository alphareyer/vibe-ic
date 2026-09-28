"""The A4 transient keeps its declared measurements while using a raw pipe."""
from __future__ import annotations

import subprocess
from pathlib import Path
import struct
import pytest

import _plugin_tree  # noqa: F401


def _deck(dump: Path) -> str:
    return ("synthetic incremental converter\n"
            "vvdd vdd 0 1\n"
            "vclk clk 0 pulse(0 1 1u 1n 1n .49u 1u)\n"
            "vin in 0 sin(.5 .2 100k)\n"
            "r1 in out 1k\n"
            "c1 out 0 1n\n"
            "* analog_incremental_decimator: mode=incremental "
            "window_clocks=10 order=2 coeff=0.25 feedback_delay_clocks=1\n"
            ".save v(out)\n.control\ntran 10n 100u\n"
            f"wrdata {dump} v(out)\n"
            "meas tran vavg avg v(out) from=20u to=80u\n"
            "meas tran vmax max v(out)\n"
            "meas tran vmin min v(out)\n"
            "let dens = vavg / 1\n"
            "echo \"MEAS density=\" $&dens\n.endc\n.end\n")


def test_a4_sends_declared_incremental_transient_through_bounded_raw_pipe(
        tmp_path, monkeypatch):
    """The actual A4 launcher must take the stream route; main takes -b deck."""
    import analog_real_corner_sweep as a4

    deck = tmp_path / "corner.sp"
    dump = tmp_path / "corner.resolution.wrdata"
    source = _deck(dump)
    deck.write_text(source)
    observed = {}
    monkeypatch.setattr(a4, "_resolve_ngspice", lambda _c: "ngspice")
    monkeypatch.setattr(a4, "_corner_reservation", lambda _c: ("1g", 1024**3))
    monkeypatch.setattr(a4, "_corner_image", lambda _c: "pinned-image")
    monkeypatch.setenv("VIBEIC_ANALOG_NUM_THREADS", "4")

    def launch(_ledger, **kw):
        observed["args"] = kw["simulation_args"]
        # Only the EDA tool's output is faked. A4 still parses its own native
        # measure grammar and identifies a real value for the caller.
        dump.write_text("0 0.5\n1e-6 0.5\n")
        return subprocess.CompletedProcess(
            kw["simulation_args"], 0,
            "vavg = 5.000000e-01\nvmax = 7.000000e-01\n"
            "vmin = 3.000000e-01\ndens = 5.000000e-01\n"
            "MEAS density=5.000000e-01\nSTREAM_COMPLETE rows=100\n", "")

    monkeypatch.setattr(a4._aca, "launch", launch)
    ok, meas, log, status = a4._run_ngspice(
        "selected", str(deck), deck_text=source, run_to_completion=True,
        corner_job={"id": "synthetic:tt:27c", "project": tmp_path,
                    "workdir": tmp_path})
    assert ok is True, (log, status)
    assert meas["dens"] == .5
    assert dump.is_file()
    command = observed["args"][-1]
    assert "stream_runner.py" in command, command
    assert "VIBEIC_ANALOG_NUM_THREADS=4" in command, command
    assert "ngspice -b" not in command, command
    runner = tmp_path / "corner.stream_runner.py"
    assert runner.is_file()
    assert '"-r"' in runner.read_text()


def test_stream_planner_rejects_unimplemented_measure_instead_of_changing_it(tmp_path):
    import analog_ngspice_stream as stream

    original = _deck(tmp_path / "out.wrdata").replace(
        "meas tran vmax max v(out)", "meas tran vmax rms v(out)")
    plan, reason = stream.plan(original)
    assert plan is None
    assert reason.startswith("unsupported_control_card")


def test_stream_simulator_deck_keeps_circuit_and_accuracy_cards(tmp_path):
    import analog_ngspice_stream as stream

    source = _deck(tmp_path / "out.wrdata").replace(
        ".save v(out)", ".options reltol=1e-5 abstol=1e-12 method=gear\n.save v(out)")
    plan, reason = stream.plan(source)
    assert reason is None
    rendered = stream.simulator_deck(source, plan)
    assert "r1 in out 1k" in rendered
    assert ".options reltol=1e-5 abstol=1e-12 method=gear" in rendered
    assert ".tran 10n 100u" in rendered
    assert ".control" not in rendered
    threaded = stream.simulator_deck(source, plan, num_threads=2)
    assert ".control\nset num_threads=2\n.endc\n.tran 10n 100u" in threaded
    assert ".options reltol=1e-5 abstol=1e-12 method=gear" in threaded


def test_raw_pipe_keeps_every_declared_measure_and_decision(tmp_path):
    import analog_ngspice_stream as stream
    import analog_incremental_decimator as decimator

    deck = _deck(tmp_path / "out.wrdata")
    plan, refusal = stream.plan(deck)
    assert refusal is None
    times = [i * 2e-7 for i in range(501)]
    vals = [float((i // 5) % 2) for i in range(501)]
    raw = tmp_path / "tool.raw"
    header = ("Title: synthetic tool output\nFlags: real\nNo. Variables: 2\n"
              "No. Points: 0       \nVariables:\n\t0\ttime\ttime\n"
              "\t1\tv(out)\tvoltage\nBinary:\n").encode()
    raw.write_bytes(header + b"".join(struct.pack("<dd", t, v)
                                      for t, v in zip(times, vals))
                    + str(len(times)).encode())
    measured, rows, last = stream._read_raw(raw, plan, tmp_path / "progress.json")
    assert rows == len(times) and last == times[-1]
    assert measured["vmax"] == 1 and measured["vmin"] == 0
    assert abs(measured["vavg"] - .5) < .02
    compact = [tuple(map(float, line.split())) for line in
               (tmp_path / "out.wrdata").read_text().splitlines()]
    c_times = [row[0] for row in compact]
    c_vals = [row[1] for row in compact]
    full_bits, _ = decimator.sample_decisions(times, vals, 1e-6, 1e-6, .5)
    compact_bits, _ = decimator.sample_decisions(c_times, c_vals, 1e-6, 1e-6, .5)
    assert compact_bits == full_bits
    assert len(compact) < len(times) / 2


def test_raw_pipe_ignores_a_title_sentence_instead_of_inventing_a_variable_table(tmp_path):
    import analog_ngspice_stream as stream

    plan, refusal = stream.plan(_deck(tmp_path / "out.wrdata"))
    assert refusal is None
    # The title may contain words that describe a point count. Only the
    # machine header's own anchored field can supply the variable table.
    raw = tmp_path / "bad.raw"
    raw.write_bytes(b"Title: No. Variables: 2 in this example\nFlags: real\n"
                    b"Variables:\n\t0\ttime\ttime\n\t1\tv(out)\tvoltage\n"
                    b"Binary:\n" + struct.pack("<dd", 0., .5) + b"1")
    with pytest.raises(ValueError, match="variable count"):
        stream._read_raw(raw, plan, tmp_path / "progress.json")
