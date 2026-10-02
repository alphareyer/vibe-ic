"""Source-owned provider census for the backend and release adapter families.

The catalogue is deliberately descriptive.  It names the real producer
callables and the canonical consumers, while the adapters keep native
qualification ``NOT_MEASURED`` until a current receipt exists.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent
BACKEND_POLICY = json.loads((HERE / "data/execution_backend_policy.json").read_text())
BACKEND_ROWS = BACKEND_POLICY["rows"]

try:
    from execution_release_rows import ROWS as RELEASE_ROWS
except ModuleNotFoundError:  # backend-first commit remains independently importable
    RELEASE_ROWS = {}


BACKEND_IDS = (
    "15", "15.5ic", "17", "18", "19", "20", "21", "22", "23", "24",
    "25", "26", "26.5ic", "27", "28", "29", "30", "32", "33", "34",
    "37", "37.3", "31",
)
RELEASE_IDS = (
    "14", "16", "35", "36", "37.4", "37.5ip", "37.5ic", "38", "39",
    "40", "41", "42", "43", "44",
)

# These are the producer sites used by the reviewed candidate.  A checker is
# listed only as a downstream consumer; it never becomes a second arm.
BACKEND_SITES = {
    "15": ("phase3_one_shot_runner.py:step_prepnr", "librelane_contract.py:run_chain"),
    "15.5ic": ("phase3_one_shot_runner.py:step_pad_ring_gen",),
    "17": ("librelane_contract.py:run_chain",),
    "18": ("librelane_contract.py:run_chain",),
    "19": ("librelane_cts_hold.py:execute",),
    "20": ("librelane_cts_hold.py:execute",),
    "21": ("librelane_route.py:execute", "librelane_postroute_repair.py:run"),
    "22": ("phase3_one_shot_runner.py:_librelane_rcx_publish",),
    "23": ("phase3_one_shot_runner.py:_emit_spef_sta", "librelane_signoff.py:run"),
    "24": ("phase3_one_shot_runner.py:_librelane_step24_record",),
    "25": ("phase3_one_shot_runner.py:_emit_ir_em_reports", "phase3_one_shot_runner.py:_emit_em_current_authority"),
    "26": ("phase3_one_shot_runner.py:_librelane_antenna_router",),
    "26.5ic": ("phase3_one_shot_runner.py:_die_finishing",),
    "27": ("phase3_one_shot_runner.py:_emit_si_timing_json", "si_signoff_timing_aware.py:run_si_signoff_timing_aware", "si_mcf_sta.py:run"),
    "28": ("phase3_one_shot_runner.py:_emit_perc_equivalent",),
    "29": ("phase3_one_shot_runner.py:_emit_sdf", "phase3_one_shot_runner.py:_step29_tool_arm"),
    "30": ("path_spice_tool.py:run_step30",),
    "32": ("phase3_one_shot_runner.py:step_postroute_repair_librelane",),
    "33": ("phase3_one_shot_runner.py:_step33_tool_arm", "librelane_signoff.py:run"),
    "34": ("phase3_one_shot_runner.py:_emit_metal_fill", "phase3_one_shot_runner.py:_emit_metal_density_report", "librelane_fill_dfm.py:run_fill_insertion"),
    "37": ("phase3_one_shot_runner.py:step_gds", "librelane_step37.py:run"),
    "37.3": ("gds_xor_check.py:main",),
    "31": ("phase3_one_shot_runner.py:step_drc", "phase3_one_shot_runner.py:step_lvs", "phase3_one_shot_runner.py:_emit_erc_report", "perc_corpus_sweep.py:main"),
}

RELEASE_SITES = {
    "14": ("flow_compliance_check.py:main", "synth_handoff_netlist_check.py:main"),
    "16": ("phase3_one_shot_runner.py:emit_clock_plan",),
    "35": ("dfm_screen_check.py:main",),
    "36": ("tapeout_checklist_gen.py:main",),
    "37.4": ("signoff_metrics_aggregate.py:aggregate",),
    "37.5ip": ("digital_hardmacro_gen.py:run", "phase3_one_shot_runner.py:_write_ip_release_docs_context"),
    "37.5ic": ("tapeout_precheck.py:main", "tapeout_docs_gen.py:main", "ic_release_docs_gen.py:main"),
    "38": ("foundry_handoff_pack_gen.py:main",),
    # 39 has a checker but no software producer when FPGA hardware is absent.
    "39": (),
    # 40--44 are physical handoffs.  They are intentionally not represented
    # by a fake Python producer.
    "40": (), "41": (), "42": (), "43": (), "44": (),
}

ENGINE_FAMILIES = {
    "15": ("openroad",), "15.5ic": ("openroad",), "17": ("openroad",),
    "18": ("openroad",), "19": ("openroad",), "20": ("openroad",),
    "21": ("openroad",), "22": ("openroad",), "23": ("opensta",),
    "24": ("openroad",), "25": ("openroad",), "26": ("openroad",),
    "26.5ic": ("openroad",), "27": ("opensta",), "28": ("openroad",),
    "29": ("iverilog",), "30": ("opensta", "ngspice"), "32": ("openroad",),
    "33": ("opensta",), "34": ("openroad",), "37": ("magic", "klayout"),
    "37.3": ("klayout",), "31": ("magic", "klayout", "netgen"),
}
RELEASE_FAMILIES = {
    "14": ("yosys",), "16": ("opensta",), "35": ("klayout",),
    "36": ("vibeic",), "37.4": ("vibeic",), "37.5ip": ("magic", "opensta"),
    "37.5ic": ("vibeic",), "38": ("vibeic",), "39": ("fpga",),
    "40": ("foundry",), "41": ("ate",), "42": ("assembly",),
    "43": ("ate",), "44": ("qualification",),
}


def _summary(items: object) -> tuple[dict, ...]:
    if isinstance(items, dict):
        items = items.get("items", ())
    if not isinstance(items, list):
        return ()
    return tuple({"id": str(x.get("id")), "kind": x.get("kind"),
                  "destination": x.get("declared_destination")}
                 for x in items if isinstance(x, dict) and x.get("id"))


def _applicability(step: str, *, release: bool, route_receipt: dict | None = None,
                   declaration: dict | None = None) -> dict[str, str]:
    """Apply the flow's condition boundary from current route evidence.

    A source-only registration without those inputs is ``unknown``.  It is
    never silently converted to an IP/IC route by the catalogue.
    """
    answers = (declaration or {}).get("answers", {}) if isinstance(declaration, dict) else {}
    deliverable = answers.get("deliverable")
    marker = (route_receipt or {}).get("marker") if isinstance(route_receipt, dict) else None
    markers = {"NO_TEMPLATE", "SELF_TAPEOUT", "slots"}
    condition_seen = marker in markers
    if step == "37.5ip":
        if deliverable == "DIE":
            return {"IC": "inapplicable: owner-attested DIE", "IP": "inapplicable: owner-attested DIE"}
        if not condition_seen:
            return {"IC": "unknown: route marker and deliverable declaration required",
                    "IP": "unknown: route marker and deliverable declaration required"}
        return {"IC": "applicable", "IP": "applicable"}
    if step in {"15.5ic", "26.5ic", "37.5ic"}:
        if deliverable == "HARDMACRO":
            return {"IC": "inapplicable: owner-attested HARDMACRO",
                    "IP": "inapplicable: owner-attested HARDMACRO"}
        if not condition_seen:
            return {"IC": "unknown: route marker and deliverable declaration required",
                    "IP": "unknown: route marker and deliverable declaration required"}
        return {"IC": "applicable", "IP": "inapplicable: IC route row"}
    if step == "39":
        return {"IC": "applicable: design-dependent FPGA evidence; absent hardware is excluded/NOT_MEASURED",
                "IP": "applicable: design-dependent FPGA evidence; absent hardware is excluded/NOT_MEASURED"}
    if step in {"40", "41", "42", "43", "44"}:
        return {"IC": "applicable: external physical handoff", "IP": "inapplicable: physical IC delivery lane"}
    return {"IC": "applicable", "IP": "applicable"}


def _backend(step: str) -> dict:
    row = BACKEND_ROWS[step]
    p = row["portfolio_policy"]
    return {
        "family": "backend",
        "step_id": step,
        "arm_id": "backend_" + step.replace(".", "_"),
        "tool_id": "opensta" if step == "33" else "ngspice" if step == "30" else "klayout" if step == "37.3" else "vibeic" if step == "31" else "librelane",
        "engine_families": list(ENGINE_FAMILIES[step]),
        "default_rank": 0,
        "disposition": "implemented",
        "runtime_status": "NOT_MEASURED",
        "producer_sites": list(BACKEND_SITES[step]),
        "consumer_gates": list(dict.fromkeys(p["mandatory_gate_programs"])),
        "canonical_outputs": list(row["canonical_row"].get("required_outputs", ())),
        "applicability": _applicability(step, release=False),
        "harvest": list(_summary(row.get("original_harvest"))),
        "notes": "One complete producer; wrappers/checkers sharing an engine family are not additional Ultra arms.",
    }


def _release(step: str) -> dict:
    row = RELEASE_ROWS[step]
    p = row["policy"]
    disposition = "implemented" if RELEASE_SITES[step] else "external" if step in {"40", "41", "42", "43", "44"} else "unavailable"
    notes = {
        "37.5ip": "Magic WriteLEF/write_abstract_lef plus STAPostPNR; preserve PG annotation because GDS route drops USE. Native engine receipt required.",
        "39": "Existing absent-FPGA exclusion is retained; no software producer is invented.",
    }.get(step, "Source-owned release composition; runtime qualification remains receipt-bound.")
    return {
        "family": "release", "step_id": step, "arm_id": "release_" + step.replace(".", "_"),
        "tool_id": "magic" if step == "37.5ip" else "vibeic" if disposition == "implemented" else "external",
        "engine_families": list(RELEASE_FAMILIES[step]), "default_rank": 0,
        "disposition": disposition, "runtime_status": "NOT_MEASURED",
        "producer_sites": list(RELEASE_SITES[step]),
        "consumer_gates": list(dict.fromkeys(p["mandatory_gate_programs"])),
        "canonical_outputs": list(row["canonical"].get("required_outputs", ())),
        "applicability": _applicability(step, release=True),
        "harvest": list(_summary(row.get("harvest"))), "notes": notes,
    }


def coverage_rows() -> list[dict]:
    return ([_backend(s) for s in BACKEND_IDS] +
            [_release(s) for s in RELEASE_IDS if s in RELEASE_ROWS])


def coverage_table() -> dict:
    rows = coverage_rows()
    return {"schema": "vibeic/provider-coverage/1", "row_count": len(rows),
            "backend_row_count": len(BACKEND_IDS), "release_row_count": len(RELEASE_IDS),
            "rows": rows}


__all__ = ["BACKEND_IDS", "RELEASE_IDS", "coverage_rows", "coverage_table"]
