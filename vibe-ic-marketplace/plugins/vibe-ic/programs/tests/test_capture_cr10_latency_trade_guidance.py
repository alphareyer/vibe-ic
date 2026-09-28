"""AI guidance is reachable, with fixed and free latency evaluation controls."""
import json
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
AGENT = PLUGIN / "agents/ic-expert-agent.md"
SKILL = PLUGIN / "skills/spec-to-rtl/SKILL.md"
CASES = Path(__file__).parent / "fixtures/capture_cr10_latency_choices.json"


def _accept(case):
    if case["latency_fixed"] and (case["candidate_input_registered"] or
                                  case["measured_latency"] != case["required_latency"]):
        return False
    return case["declared_latency"] == case["measured_latency"]


def test_guidance_reaches_author_and_declares_the_edge_origin():
    text = AGENT.read_text()
    section = text.split("### Skill: registering a high-fanout serial input is a latency trade — declare it", 1)[1]
    section = section.split("### Skill:", 1)[0]
    assert "rising edge 0" in section
    assert "latency_cycles" in section
    assert "latency is fixed" in section and "latency is explicitly free" in section
    assert "input-to-first-flop" in section
    assert "registering a high-fanout serial input is a latency trade" in SKILL.read_text()


def test_two_other_designs_have_positive_and_negative_controls():
    cases = json.loads(CASES.read_text())
    assert len({c["design"] for c in cases}) == 2
    for design in {c["design"] for c in cases}:
        group = [c for c in cases if c["design"] == design]
        assert {c["accept"] for c in group} == {True, False}
    assert all(_accept(c) == c["accept"] for c in cases)
