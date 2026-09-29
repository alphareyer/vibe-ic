"""Declared-contract in-order transaction watchdog RTL (not prose inference).

BLOCKING at the caller: invalid or unsupported declarations raise ValueError;
they must not fall through to a guessed timer. Emission is a candidate, never
functional acceptance. The caller/AI supplies policy and source citations;
normal independent RTL validation/review still checks that mapping.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from rtl_hygiene_lint import VERILOG_KEYWORDS

CONTRACT_REL = Path("input/in_order_watchdog.json")
SCHEMA = "vibeic.in_order_watchdog.v1"
ROLES = {"clock", "reset", "request_valid", "request_ready", "response_done",
         "threshold", "timed_out", "violation"}
POLICIES = {"counter_bits", "max_accepted", "start", "response_order",
            "timeout_compare", "initial_age", "saturation", "reset", "flag",
            "zero_latency", "violation_policy", "request_protocol"}
_RESERVED = VERILOG_KEYWORDS | {"interface", "endinterface", "class", "endclass",
                              "program", "endprogram", "package", "endpackage"}


def _identifier(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)
            or value in _RESERVED):
        raise ValueError("WATCHDOG_IDENTIFIER_INVALID")
    return value


def validate_contract(c: dict) -> dict:
    if not isinstance(c, dict) or set(c) != POLICIES | {"schema", "module", "ports", "mapping"}:
        raise ValueError("WATCHDOG_CONTRACT_FIELDS_UNDECLARED_OR_UNKNOWN")
    if c["schema"] != SCHEMA:
        raise ValueError("WATCHDOG_SCHEMA_UNSUPPORTED")
    _identifier(c["module"])
    if not isinstance(c["ports"], dict) or set(c["ports"]) != ROLES:
        raise ValueError("WATCHDOG_PORT_ROLES_INCOMPLETE")
    names = [_identifier(v) for v in c["ports"].values()]
    if len(set(names)) != len(names):
        raise ValueError("WATCHDOG_PORT_ROLES_COLLIDE")
    for key, limit in (("counter_bits", 64), ("max_accepted", 1024)):
        if type(c[key]) is not int or not 1 <= c[key] <= limit:
            raise ValueError("WATCHDOG_" + key.upper() + "_UNSUPPORTED")
    choices = {"start": {"accepted", "first_valid"},
               "response_order": {"in_order"}, "timeout_compare": {"next_age_ge"},
               "saturation": {"max"}, "flag": {"pulse", "sticky_until_reset"},
               "zero_latency": {"complete_on_accept"},
               "request_protocol": {"hold_valid_until_accepted"},
               "violation_policy": {"sticky_until_reset"}}
    for key, supported in choices.items():
        if not isinstance(c[key], str) or c[key] not in supported:
            raise ValueError("WATCHDOG_" + key.upper() + "_UNSUPPORTED")
    if type(c["initial_age"]) is not int or c["initial_age"] != 0:
        raise ValueError("WATCHDOG_INITIAL_AGE_UNSUPPORTED")
    reset = c["reset"]
    if (not isinstance(reset, dict) or set(reset) != {"polarity", "synchrony"}
            or reset["polarity"] not in ("high", "low")
            or reset["synchrony"] not in ("sync", "async")):
        raise ValueError("WATCHDOG_RESET_UNDECLARED_OR_UNSUPPORTED")
    m = c["mapping"]
    if (not isinstance(m, dict) or set(m) != {"actor", "source", "sha256", "citations"}
            or m["actor"] not in ("owner", "AI")
            or not isinstance(m["source"], str)
            or not isinstance(m["sha256"], str)
            or not re.fullmatch("[0-9a-f]{64}", m["sha256"])
            or not isinstance(m["citations"], dict)
            or set(m["citations"]) != POLICIES | {"module", "ports"}
            or any(not isinstance(q, str) or not q.strip() for q in m["citations"].values())):
        raise ValueError("WATCHDOG_POLICY_MAPPING_INCOMPLETE")
    return c


def load_declared(project: Path) -> dict | None:
    """No declaration => not applicable; present-but-bad => named refusal.

    Only a project's public input document can ground the mapping. Quotes and
    source hash establish identity, not entailment: AI review must still check
    that each declared choice follows the quoted input.
    """
    project = Path(project).resolve()
    path = project / CONTRACT_REL
    if not path.exists():
        if path.is_symlink():
            raise ValueError("WATCHDOG_CONTRACT_DANGLING_LINK")
        return None
    path.resolve().relative_to(project / "input")
    declared_bytes = path.read_bytes()
    c = validate_contract(json.loads(declared_bytes))
    source = (project / c["mapping"]["source"]).resolve()
    source.relative_to(project / "input")
    if source == path.resolve() or source.suffix.lower() not in (".md", ".txt"):
        raise ValueError("WATCHDOG_MAPPING_SOURCE_NOT_PUBLIC_DOCUMENT")
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != c["mapping"]["sha256"]:
        raise ValueError("WATCHDOG_MAPPING_SOURCE_CHANGED")
    text = raw.decode("utf-8")
    if any(q not in text for q in c["mapping"]["citations"].values()):
        raise ValueError("WATCHDOG_MAPPING_CITATION_ABSENT")
    return {"shape": "in_order_watchdog", "module": c["module"], "rtl": emit(c),
            "contract_sha256": hashlib.sha256(declared_bytes).hexdigest(),
            "source_sha256": c["mapping"]["sha256"], "mapping_actor": c["mapping"]["actor"]}


def emit(c: dict) -> str:
    c = validate_contract(c)
    p = c["ports"]
    bits = c["counter_bits"]
    slots = c["max_accepted"] + (c["start"] == "first_valid")
    count_bits = max(1, slots.bit_length())
    prefix = "wd_"
    while any(n.startswith(prefix) for n in p.values()):
        prefix += "_"
    # Internal identifiers cannot shadow any declared port.
    n = lambda s: prefix + s
    reset_test = p["reset"] if c["reset"]["polarity"] == "high" else "!" + p["reset"]
    edges = "posedge " + p["clock"]
    if c["reset"]["synchrony"] == "async":
        edges += " or " + ("posedge " if c["reset"]["polarity"] == "high" else "negedge ") + p["reset"]
    start = (p["request_valid"] + " && !" + n("seen") if c["start"] == "first_valid"
             else p["request_valid"] + " && " + p["request_ready"])
    flag_update = (f"if ({n('hit')}) {p['timed_out']} <= 1'b1;" if c["flag"] == "sticky_until_reset"
                   else f"{p['timed_out']} <= {n('hit')};")
    return f"""// Declared in-order watchdog. Age zero at start; existing next age is
// compared at the sampling edge. Threshold zero also fires for a new entry.
// Completion removes only the oldest; it never clears another entry's age.
// Capacity: {c['max_accepted']} accepted + {int(c['start'] == 'first_valid')} stalled-valid slot.
// Mapping is candidate input, not acceptance. Overflow/orphan completion is
// a sticky contract violation, never silent dropped work.
module {c['module']} (
    input {p['clock']}, input {p['reset']},
    input {p['request_valid']}, input {p['request_ready']},
    input {p['response_done']}, input [{bits-1}:0] {p['threshold']},
    output reg {p['timed_out']}, output reg {p['violation']}
);
    reg [{bits-1}:0] {n('ages')} [0:{slots-1}];
    reg [{count_bits-1}:0] {n('count')};
    reg {n('seen')};
    integer {n('j')}, {n('k')};
    wire [{count_bits-1}:0] {n('accepted')} = {n('count')} -
        (({int(c['start'] == 'first_valid')} && {n('seen')} && {n('count')} != 0) ? 1'b1 : 1'b0);
    wire {n('accept_stalled')} = {int(c['start'] == 'first_valid')} && {n('seen')}
        && {p['request_valid']} && {p['request_ready']} && {n('count')} != 0;
    wire {n('retire')} = {p['response_done']} && ({n('count')} != 0)
        && ({n('accepted')} != 0 || {n('accept_stalled')});
    wire {n('start')} = {start};
    wire {n('immediate')} = {p['response_done']} && ({n('count')} == 0)
                            && {n('start')} && {p['request_ready']};
    wire {n('enqueue')} = {n('start')} && !{n('immediate')};
    wire [{count_bits-1}:0] {n('retained')} = {n('count')} - ({n('retire')} ? 1'b1 : 1'b0);
    wire {n('fault')} = ({n('enqueue')} && {n('retained')} == {slots})
        || ({p['response_done']} && {n('accepted')} == 0
            && !{n('immediate')} && !{n('accept_stalled')})
        || ({n('seen')} && !{p['request_valid']})
        || ({p['request_valid']} && {p['request_ready']}
            && ({n('accepted')} - (({n('retire')} && {n('accepted')} != 0) ? 1'b1 : 1'b0))
                >= {c['max_accepted']});
    reg {n('hit')};
    function [{bits-1}:0] {n('age_next')}(input [{bits-1}:0] age);
        {n('age_next')} = (&age) ? age : age + {bits}'d1;
    endfunction
    always @* begin
        {n('hit')} = 1'b0;
        for ({n('k')}=0; {n('k')}<{slots}; {n('k')}={n('k')}+1) begin
            if ({n('k')} < {n('count')} && !({n('retire')} && {n('k')}==0)
                && {{1'b0,{n('ages')}[{n('k')}]}} + {bits+1}'d1 >= {{1'b0,{p['threshold']}}})
                {n('hit')} = 1'b1;
        end
        if ({n('enqueue')} && {p['threshold']} == 0) {n('hit')} = 1'b1;
    end
    always @({edges}) begin
        if ({reset_test}) begin
            {n('count')} <= 0;
            {n('seen')} <= 0;
            {p['timed_out']} <= 0;
            {p['violation']} <= 0;
            for ({n('j')}=0; {n('j')}<{slots}; {n('j')}={n('j')}+1) {n('ages')}[{n('j')}] <= 0;
        end else begin
            {n('seen')} <= {p['request_valid']} && !{p['request_ready']};
            {n('count')} <= {n('retained')};
            for ({n('j')}=0; {n('j')}<{slots}; {n('j')}={n('j')}+1) begin
                if ({n('j')} < {n('retained')}) begin
                    if ({n('retire')}) {n('ages')}[{n('j')}] <= {n('age_next')}({n('ages')}[{n('j')}+1]);
                    else {n('ages')}[{n('j')}] <= {n('age_next')}({n('ages')}[{n('j')}]);
                end else {n('ages')}[{n('j')}] <= 0;
            end
            if ({n('enqueue')} && {n('retained')} < {slots}) begin
                {n('ages')}[{n('retained')}] <= 0;
                {n('count')} <= {n('retained')} + 1'b1;
            end
            {flag_update}
            if ({n('fault')}) {p['violation']} <= 1'b1;
        end
    end
endmodule
"""
