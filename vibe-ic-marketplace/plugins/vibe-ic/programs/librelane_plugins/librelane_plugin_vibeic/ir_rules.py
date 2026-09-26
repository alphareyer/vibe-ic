"""The step-24 IR rule, as one pure function over LibreLane's own metrics.

Two readers apply it, and both import it from here so they cannot drift:

* `Checker.VibeicIRDrop` (this package's LibreLane step), which fails a
  LibreLane run the way the tool's own checkers do;
* `librelane_ir_antenna.judge_ir` (vibe-ic), which records the verdict from
  the `OpenROAD.IRDropReport` state even when the checker stopped the chain.

What it reads: `design_powergrid__drop__worst__net:<net>`, which OpenROAD PSM
writes once per analysed net and LibreLane aggregates.  It never reads
`ir__drop__worst`: LibreLane parses that key with `re.search`, so it is the
FIRST net's drop, not the worst (measured on spm: VDD 0.869 mV first, VSS
1.19 mV worst, `ir__drop__worst` 0.000869).

Rules (the harvest of the direct path's `psm_analysis_coverage` and budget
verdict):

* every declared supply net (VDD_NETS + GND_NETS) must carry its per-net
  metric; a declared net with none was not analysed, and the worst over the
  nets that were is a number about a smaller design (#669);
* the worst drop over ALL analysed nets is compared with the declared budget,
  a percent of the supply voltage;
* a supply voltage that is absent or zero is NOT_MEASURED, never a pass (#662);
* no budget declared is NOT_MEASURED, never a pass (#2109: a solver outcome is
  never laundered into a skip).

chip-AGNOSTIC: net names come from the config; no rail, PDK or design literal.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

PER_NET_PREFIX = "design_powergrid__drop__worst__net:"


def per_net_worst(metrics: Dict[str, Any]) -> Dict[str, float]:
    """`{net: worst drop in V}` from the per-net keys without a corner suffix."""
    out: Dict[str, float] = {}
    for key, value in (metrics or {}).items():
        if not key.startswith(PER_NET_PREFIX):
            continue
        net = key[len(PER_NET_PREFIX):]
        if "__corner:" in net:
            continue
        try:
            out[net] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def ir_findings(metrics: Dict[str, Any], vdd_nets: Iterable[str],
                gnd_nets: Iterable[str], supply_v: Optional[float],
                budget_pct: Optional[float]) -> Dict[str, Any]:
    """The verdict and every number it rests on."""
    declared: List[str] = list(dict.fromkeys([*(vdd_nets or []), *(gnd_nets or [])]))
    worst = per_net_worst(metrics)
    missing = [net for net in declared if net not in worst]
    extra = sorted(set(worst) - set(declared))
    doc: Dict[str, Any] = {
        "declared_nets": declared, "analysed_nets": sorted(worst),
        "unanalysed_declared_nets": missing, "undeclared_analysed_nets": extra,
        "per_net_worst_drop_v": dict(sorted(worst.items())),
        "supply_v": supply_v, "budget_pct": budget_pct,
    }
    if not declared:
        doc.update(verdict="NOT_MEASURED", reason="no supply net is declared")
        return doc
    if missing:
        doc.update(verdict="FAIL",
                   reason=f"declared supply net(s) not analysed: {', '.join(missing)}")
        return doc
    worst_net = max(worst, key=lambda net: worst[net])
    doc["worst_net"] = worst_net
    doc["worst_drop_v"] = worst[worst_net]
    if not supply_v:
        doc.update(verdict="NOT_MEASURED", reason="no supply voltage was determined")
        return doc
    doc["worst_drop_pct"] = 100.0 * worst[worst_net] / float(supply_v)
    if budget_pct is None:
        doc.update(verdict="NOT_MEASURED", reason="no IR budget is declared")
        return doc
    doc["budget_v"] = float(budget_pct) / 100.0 * float(supply_v)
    doc["verdict"] = "PASS" if worst[worst_net] <= doc["budget_v"] else "FAIL"
    if doc["verdict"] == "FAIL":
        doc["reason"] = (f"{worst_net} drops {worst[worst_net]:.6g} V, over the "
                         f"{float(budget_pct):g}% budget of {doc['budget_v']:.6g} V")
    return doc
