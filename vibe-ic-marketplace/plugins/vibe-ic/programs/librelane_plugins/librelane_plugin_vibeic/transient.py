"""The transient (dynamic) IR record, per net, from the fork's PSM report.

One reader: `dynamic_ir_vectored_emit`'s own parsers and `build_result`, so
the tool step and the direct emitter cannot disagree about the grammar or the
`scaled_static_bound` label.  Pure over text.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # vibe-ic programs/

NET_MARKER = "=== VIBEIC_TRANSIENT_NET "


def transient_findings(report: str, nets: List[str], voltage: float,
                       period_ns: float, period_source: str) -> Dict[str, Any]:
    import dynamic_ir_vectored_emit as dyn
    blocks: Dict[str, str] = {}
    for chunk in report.split(NET_MARKER)[1:]:
        name, _, body = chunk.partition("\n")
        blocks[name.strip()] = body
    per_net: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    for net in nets:
        body = blocks.get(net, "")
        drop = dyn.parse_worst_dynamic_ir_v(body)
        if drop is None:
            missing.append(net)
            continue
        per_net[net] = {"dynamic_drop_v": drop,
                        "static_drop_v": dyn.parse_worst_static_tr_v(body),
                        "ratio": dyn.parse_dynamic_static_ratio(body),
                        "capacitance_model": dyn.parse_cap_model(body)}
    if not nets or missing:
        return {"verdict": "NOT_MEASURED", "per_net": per_net,
                "reason": ("no supply net declared" if not nets else
                           f"no dynamic IR line for net(s) {missing} (a solver outcome, "
                           "never a skip)")}
    worst = max(per_net, key=lambda n: per_net[n]["dynamic_drop_v"])
    row = per_net[worst]
    body = blocks[worst]
    result: Dict[str, Any] = dyn.build_result(
        row["dynamic_drop_v"] * 1000.0, voltage,
        row["static_drop_v"] * 1000.0 if row["static_drop_v"] is not None else None,
        row["ratio"], None, worst, period_ns, period_source,
        dyn.parse_steps(body), dyn.parse_timestep_s(body), dyn.parse_current_model(body),
        row["capacitance_model"])
    # Every net's model must agree before one label covers them all.
    genuine = [r["capacitance_model"] for r in per_net.values()]
    result["scaled_static_bound"] = not all(
        isinstance(m, str) and m.startswith("on-die-cap") for m in genuine)
    result.update(verdict="MEASURED", per_net=per_net, nets_analysed=sorted(per_net),
                  worst_dynamic_drop_v=row["dynamic_drop_v"])
    return result
