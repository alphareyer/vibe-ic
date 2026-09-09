"""An omitted required port and an invented one must not read the same (vibe-ic#2210).

MEASURED in the issue, against plugin 1.19.82:

    | synthetic case                              | joint compile | product result |
    | required `io_line` omitted by the candidate |             2 | INVALID        |
    | conforming candidate, test invents `io_lien`|             2 | INVALID        |
    | conforming candidate, required-port test    |             0 | PASS           |

Both INVALIDs said only `joint compile failed; errors cite only the challenge file`.
iverilog reports both at the challenge's named-port instantiation, so attribution BY
CITED FILE cannot separate them — and they are opposite verdicts. The first is a real
interface defect being held without a candidate-side proof; the second is correctly
INVALID.

Deciding which one it is needs the PROMPT-BOUND required interface, and
`_run_verification_challenge` receives only a candidate and a challenge. So it must not
decide. What it must do is stop being silent: name the ports, and say which question is
unanswered. A gate that cannot decide has to say so rather than pick one, or the next
reader inherits a verdict with nothing under it.
"""
import re

import _plugin_tree  # noqa: F401
import benchmark_dispatch as BD


def test_the_absent_port_is_named_not_swallowed():
    errors = ("challenge_required.sv:9: error: port ``io_line'' is not a port of dut.\n"
              "1 error(s) during elaboration.\n")
    assert BD._ports_absent_from_dut(errors) == ["io_line"], errors


def test_an_invented_port_is_named_the_same_way():
    """Both cases name their port. The point is not to tell them apart here — it is
    that neither disappears, so a consumer holding the interface CAN tell them apart."""
    errors = "challenge_invented.sv:10: error: port ``io_lien'' is not a port of dut.\n"
    assert BD._ports_absent_from_dut(errors) == ["io_lien"], errors


def test_membership_not_count_and_no_duplicates():
    """One missing port is reported once per instantiation site. The finding is WHICH
    ports, never how many diagnostics mentioned them."""
    errors = ("a.sv:9: error: port ``io_line'' is not a port of dut.\n"
              "a.sv:9: error: port ``io_line'' is not a port of dut.\n"
              "a.sv:9: error: port ``clk_i'' is not a port of dut.\n")
    assert BD._ports_absent_from_dut(errors) == ["io_line", "clk_i"], errors


def test_an_unrelated_compile_error_names_no_port():
    """The extractor must be able to return nothing. One that always finds a port would
    attach this disclosure to every failure and stop meaning anything."""
    errors = ("cand.v:4: syntax error\n"
              "cand.v:4: error: invalid module item.\n")
    assert BD._ports_absent_from_dut(errors) == [], errors


def test_the_undetermined_attribution_is_stated_in_the_source():
    """The disclosure is the deliverable of #2210, so pin its two load-bearing claims:
    that the ports are named, and that attribution is explicitly UNDETERMINED rather
    than silently resolved to INVALID."""
    src = (BD.__file__ and open(BD.__file__).read()) or ""
    assert "ATTRIBUTION UNDETERMINED" in src, "the undecidable must be named, not implied"
    assert "ports_absent_from_candidate" in src, (
        "the verdict must carry the port membership so a consumer holding the "
        "prompt-bound interface can attribute it")
    assert re.search(r"do not read this\s+\"?\s*\"?INVALID as evidence", src) or \
        "do not read this" in src, "the INVALID must warn against being read as conformance"
