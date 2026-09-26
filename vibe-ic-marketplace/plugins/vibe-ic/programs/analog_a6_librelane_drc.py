#!/usr/bin/env python3
"""analog_a6_librelane_drc.py — A6 per-block DRC through LibreLane (T94).

Runs LibreLane `KLayout.DRC` (the PDK's sign-off runset, as LibreLane's PDK
configuration names it) and `Magic.DRC` on the block's A5 GDS, through
`librelane_contract.run_chain` (`python3 -m librelane.steps run`, GDS-only
state), and writes `phase3/analog/<block>/a6_librelane_drc.json`:

  * per engine: the step's own metric (`klayout__drc_error__count`,
    `magic__drc_error__count`) and the violations per rule read from the
    step's own lyrdb report;
  * the GRADED-RULE UNION: every rule the KLayout runset graded (its lyrdb
    lists each graded rule as a category, fired or not), and every rule Magic
    fired that the runset does not grade;
  * the native A6 attribution beside it: a Magic rule the sign-off deck does
    not grade is only ever excused when A6's own attribution
    (`analog_a6_drc_attribute`) classes it as the PDK's own gencell
    (`DEVICE_CELL`). Anything else is `blocking`.

LibreLane itself runs the two engines separately and compares only totals
(ERROR_ON_MAGIC_DRC / ERROR_ON_KLAYOUT_DRC); the union and the attribution are
the vibe-ic layer on top of the tool's output.

MEASURED on vibeic-eda 0.3.77 / u_hawaii_adc/ldo: KLayout.DRC 549 rules
graded, 0 violations; Magic.DRC 64 = M2.d x60 (DEVICE_CELL per A6's own
attribution) + MIM.e x4 on the GDS. The native second engine reads
`layout.mag`, not the GDS, and never saw MIM.e.

Exit codes: 0 record written and nothing blocking; 1 record written with a
blocking rule; 2 honest gap (no GDS / no layout record); 69 environment
refusal. chip-AGNOSTIC.
"""
from __future__ import annotations

import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

import _analog_producer_common as _pc
from _atomic_artefact import write_json

PRODUCER = "analog_a6_librelane_drc"
ENGINES = (("KLayout.DRC", "klayout__drc_error__count", "drc.klayout.lyrdb"),
           ("Magic.DRC", "magic__drc_error__count", "drc.magic.lyrdb"))


def _rule_name(raw: Optional[str]) -> str:
    return (raw or "").strip().strip("'\"").strip()


def lyrdb_rules(path: Path) -> Dict[str, object]:
    """`{"graded": [...], "violations": {rule: n}}` from a KLayout report
    database. Each `<category>` is a rule the engine GRADED; each `<item>`
    one violation of the rule it names. Parsed as XML, never scraped."""
    root = ET.parse(path).getroot()
    graded = sorted({_rule_name(c.findtext("name"))
                     for c in root.iter("category")} - {""})
    hits = Counter(_rule_name(i.findtext("category")) for i in root.iter("item"))
    hits.pop("", None)
    return {"graded": graded, "violations": dict(sorted(hits.items()))}


def union(klayout: dict, magic: dict, device_cell_rules: set) -> dict:
    """The graded-rule union of the two engines' outputs."""
    graded = set(klayout["graded"])
    ungraded_fired = {r: n for r, n in magic["violations"].items()
                      if r not in graded}
    excused = {r: n for r, n in ungraded_fired.items() if r in device_cell_rules}
    blocking = dict(klayout["violations"])
    blocking.update({f"magic:{r}": n for r, n in ungraded_fired.items()
                     if r not in device_cell_rules})
    return {"signoff_rules_graded": len(graded),
            "signoff_violations": klayout["violations"],
            "magic_rules_the_signoff_deck_does_not_grade": ungraded_fired,
            "magic_deferred_to_signoff_deck": {
                r: n for r, n in magic["violations"].items() if r in graded},
            "excused_as_pdk_device_cell": excused,
            "blocking": blocking}


def native_device_cell_rules(attribution: Optional[dict]) -> set:
    """Rule ids A6's own attribution classes as the PDK's gencell."""
    import analog_a6_native_pv as nat
    out = set()
    rules = (((attribution or {}).get("by_class_and_rule") or {})
             .get("DEVICE_CELL") or {})
    for message in rules:
        rid = nat.rule_id(message)
        if rid:
            out.add(rid)
    return out


def run(project: Path, block: str, image: str,
        attribution: Optional[dict] = None) -> int:
    import librelane_contract as lc
    import analog_a7_post_layout_emit as a7

    bdir = project / "phase3" / "analog" / block
    out = bdir / "a6_librelane_drc.json"
    record: dict = {"producer": PRODUCER, "schema": 1, "block": block,
                    "image": image}
    gds = bdir / f"{block}.gds"
    try:
        tech = a7.layout_tech(bdir)
    except ValueError as exc:
        tech = None
        why = str(exc)
    if not gds.is_file() or tech is None:
        print(f"{_pc.HONEST_GAP_TOKEN} {PRODUCER}: "
              f"{'no ' + gds.name if not gds.is_file() else why}",
              file=sys.stderr)
        return 2
    pdk, pdk_root = tech.parents[2].name, str(tech.parents[3])
    record.update({"pdk": pdk, "pdk_root": pdk_root})
    work = bdir / "a6_librelane"
    work.mkdir(parents=True, exist_ok=True)
    state_in = work / "state_in.json"
    write_json(state_in, {"gds": str(gds.resolve()), "metrics": {}})
    engines: Dict[str, dict] = {}
    for step, metric, report in ENGINES:
        slug = step.lower().replace(".", "_")
        src = work / f"{slug}.src.json"
        write_json(src, {"meta": {"version": 2, "step": step},
                         "DESIGN_NAME": block, "PDK": pdk})
        try:
            cfg = lc.resolve_step_config(project, image, src,
                                         work / f"{slug}.json",
                                         pdk_root=pdk_root)
            resolved = json.loads(cfg.read_text())
            resolved.setdefault("meta", {})["step"] = step
            write_json(cfg, resolved)
            folder = lc.run_chain(project, image, [(step, cfg, state_in)],
                                  namespace=f"analog/{block}/a6_{slug}",
                                  pdk_root=pdk_root)[-1]
        except lc.Refusal as exc:
            rc = _pc.EX_ENV_REFUSED if exc.code in (
                "LL_IMAGE_INCAPABLE", "LL_CONFIG_RESOLVE_FAILED") else 1
            record.update({"result": "NOT_MEASURED", "rule": exc.code,
                           "detail": str(exc)})
            write_json(out, record)
            tok = _pc.ENV_REFUSED_TOKEN if rc == _pc.EX_ENV_REFUSED else "FAIL:"
            print(f"{tok} {PRODUCER} {step}: {exc}", file=sys.stderr)
            return rc
        judged = lc.judge_step(folder, [metric], work / f"{slug}.judge.json",
                               required_reports=[f"reports/{report}"],
                               scope={"block": block, "gds": str(gds)})
        rpt = folder / "reports" / report
        rules = lyrdb_rules(rpt) if rpt.is_file() else {"graded": [],
                                                        "violations": {}}
        engines[step] = {"step_dir": str(folder.relative_to(project)),
                         "judge": judged["verdict"],
                         "metric": judged["metrics"][metric],
                         **rules}
        n = judged["metrics"][metric].get("value")
        if isinstance(n, int) and n != sum(rules["violations"].values()):
            engines[step]["report_disagrees_with_metric"] = True
    k, m = engines["KLayout.DRC"], engines["Magic.DRC"]
    if k["judge"] != "PASS" or not k["graded"]:
        record.update({"engines": engines, "result": "NOT_MEASURED",
                       "rule": "A6_LL_SIGNOFF_GRADED_NOTHING",
                       "detail": "the KLayout runset graded no rule — silence "
                                 "is not a clean deck"})
        write_json(out, record)
        print(f"FAIL: {PRODUCER}: {record['detail']}", file=sys.stderr)
        return 1
    u = union(k, m, native_device_cell_rules(attribution))
    record.update({"engines": {s: {kk: vv for kk, vv in e.items()
                                   if kk != "graded"}
                               for s, e in engines.items()},
                   "union": u,
                   "result": "BLOCKING" if u["blocking"] else "CLEAN"})
    write_json(out, record)
    print(f"[{PRODUCER}] block={block} KLayout.DRC graded "
          f"{u['signoff_rules_graded']} rule(s), "
          f"{sum(u['signoff_violations'].values())} violation(s); Magic.DRC "
          f"{sum(m['violations'].values())} violation(s); blocking="
          f"{u['blocking'] or 'none'}")
    return 1 if u["blocking"] else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = _pc.ProducerArgumentParser(prog=PRODUCER,
                                    description=__doc__.split("\n")[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--block", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--attribution", type=Path, default=None,
                    help="analog_a6_drc_attribute --json report for the block")
    a = ap.parse_args(argv)
    att = None
    if a.attribution and a.attribution.is_file():
        att = json.loads(a.attribution.read_text())
        if isinstance(att.get("blocks"), dict):
            att = att["blocks"].get(a.block, att)
    return run(a.project.resolve(), a.block, a.image, att)


if __name__ == "__main__":
    sys.exit(main())
