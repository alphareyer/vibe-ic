"""The transient (dynamic) IR record, per net, from the fork's PSM report.

One reader: `dynamic_ir_vectored_emit`'s own parsers, `decap_effect` and
`build_result`, so the tool step and the direct emitter cannot disagree about
the grammar, the decap's unit or the `scaled_static_bound` label.  Pure over
text.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # vibe-ic programs/

NET_MARKER = "=== VIBEIC_TRANSIENT_NET "
REF_MARKER = "=== VIBEIC_TRANSIENT_REF "
_MARK_RE = re.compile(r"^=== VIBEIC_TRANSIENT_(NET|REF) (\S+)[ \t]*$", re.M)


def transient_blocks(report: str) -> Dict[tuple, str]:
    """{("psm"|"ref", net): text}: the reported solve and its reference."""
    marks = list(_MARK_RE.finditer(report))
    out: Dict[tuple, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(report)
        out[("psm" if m.group(1) == "NET" else "ref", m.group(2))] = report[m.end():end]
    return out


def transient_findings(report: str, nets: List[str], voltage: float,
                       period_ns: float, period_source: str,
                       decap_f: Optional[float] = None) -> Dict[str, Any]:
    import dynamic_ir_vectored_emit as dyn
    blocks = transient_blocks(report)
    per_net: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    for net in nets:
        body = blocks.get(("psm", net), "")
        drop = dyn.parse_worst_dynamic_ir_v(body)
        if drop is None:
            missing.append(net)
            continue
        per_net[net] = {"dynamic_drop_v": drop,
                        "static_drop_v": dyn.parse_worst_static_tr_v(body),
                        "ratio": dyn.parse_dynamic_static_ratio(body),
                        "capacitance_model": dyn.parse_cap_model(body)}
        if decap_f is not None:
            per_net[net]["decap"] = dyn.decap_effect(blocks.get(("ref", net), ""),
                                                     body, decap_f)
    if not nets or missing:
        return {"verdict": "NOT_MEASURED", "per_net": per_net,
                "reason": ("no supply net declared" if not nets else
                           f"no dynamic IR line for net(s) {missing} (a solver outcome, "
                           "never a skip)")}
    unmodelled = [n for n, r in per_net.items()
                  if (r.get("decap") or {}).get("state") == "DECAP_READBACK_MISMATCH"]
    if unmodelled:
        return {"verdict": "NOT_MEASURED", "per_net": per_net,
                "reason": "; ".join(f"{n}: {per_net[n]['decap'].get('reason')}"
                                    for n in unmodelled)}
    worst = max(per_net, key=lambda n: per_net[n]["dynamic_drop_v"])
    row = per_net[worst]
    body = blocks[("psm", worst)]
    # One label covers every net only when every net earned it.
    decap = None
    if decap_f is not None:
        decap = dict(row["decap"], genuine=all(
            r["decap"].get("genuine") is True for r in per_net.values()))
    result: Dict[str, Any] = dyn.build_result(
        row["dynamic_drop_v"] * 1000.0, voltage,
        row["static_drop_v"] * 1000.0 if row["static_drop_v"] is not None else None,
        row["ratio"], None, worst, period_ns, period_source,
        dyn.parse_steps(body), dyn.parse_timestep_s(body), dyn.parse_current_model(body),
        row["capacitance_model"], decap=decap)
    result.update(verdict="MEASURED", per_net=per_net, nets_analysed=sorted(per_net),
                  worst_dynamic_drop_v=row["dynamic_drop_v"])
    return result
