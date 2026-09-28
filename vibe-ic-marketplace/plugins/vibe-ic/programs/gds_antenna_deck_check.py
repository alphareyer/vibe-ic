#!/usr/bin/env python3
"""gds_antenna_deck_check.py — independent GDS-geometry process-antenna sign-off.

WHAT GAP THIS CLOSES
--------------------
Step 26 (antenna) previously had exactly ONE opinion: `antenna_report_check.py`,
which parses the count OpenROAD's own `check_antennas` wrote. That is a
ROUTER-REPORT consumer — it can only repeat what the router already believed. If
the router's antenna model misses a violation, the report is clean and the gate
passes, with no second opinion anywhere in the flow.

This gate adds the missing INDEPENDENT GDS opinion. When the selected PDK ships
a native KLayout antenna deck, it runs that deck on the STREAMED GDS and judges
the deck's own ANT rules. The PDK bridge receipt selects the tree; the report
binds the PDK rule, runnable parent, GDS, RDB and transcript by SHA-256.

For a PDK that instead declares a JSON geometry config, it runs the KLayout
fork's GDS-geometry engine (`gds_antenna/antenna_check.py`) and computes, per
metal layer, the per-net antenna ratio
    ratio = (connected metal area) / (connected gate area)
from the as-fabricated geometry, using STAGED connectivity (at the layer-k etch
stage only layers 1..k exist, so an upper-metal jumper cannot relieve a
lower-stage antenna — a relief the router's final-netlist view credits) plus
cumulative-antenna-area (CAA) charge sharing, which no per-layer check can see.
Those staged/CAA claims apply to the JSON engine; the native path measures the
rules its PDK actually executes and does not infer an extra rule class.

It does NOT replace `antenna_report_check` — both run at Step 26. Where a router
report exists, the two independent counts are CROSS-CHECKED
(`gds_antenna/xcheck_router.py`): a clean/dirty DISAGREEMENT is a hard FAIL, so a
router-vs-geometry inconsistency is SURFACED rather than averaged away.

HONEST DEGRADATION (§4.05)
--------------------------
A step that passes because the checker never ran is a false PASS. Every
non-execution here is a NAMED, DISCLOSED skip written into the report JSON and
printed with the plugin's `VACUOUS_PASS:` sentinel at rc 2, which
`flow_compliance_check` renders as VACUOUS-PASS — never a bare PASS. Note the
exit-code remap versus the fork's reference wrapper: the fork used rc 3 for
honest-skip, but rc 3 is this plugin's PASS_WITH_WAIVERS code and a rc 3 without
the waiver sentinel reads as FAIL, so the disclosed skip is rc 2 here.

A deck config that IS present but matches no gate geometry is a FAIL, not a skip:
the PDK declared an antenna deck and it did not work, which is exactly the
"checker didn't run" case that must never read as clean. `--strict` additionally
turns every disclosed skip into a FAIL (tapeout use).

chip/PDK-AGNOSTIC: the layer stack and the per-metal ratio bounds come entirely
from the caller's deck config; no vendor, foundry or design literal appears here.

    gds_antenna_deck_check <project_dir> [--gds G] [--config C] [--router R]
                           [--cell TOP] [--json OUT] [--strict]
    main(argv) -> int : 0 PASS / 1 FAIL / 2 DISCLOSED SKIP
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from . import _klayout_launch as _kl                     # type: ignore
except ImportError:                                          # standalone gate
    import _klayout_launch as _kl                            # type: ignore
from _atomic_artefact import write_bytes as _atomic_write_bytes  # vibe-ic#1082

PASS, FAIL, SKIP = 0, 1, 2

# Where a streamed GDS lands, most-final first. Globs only — no design literal.
_GDS_GLOBS = (
    "phase3/stage4/gds/*.gds",
    "phase3/stage3/pnr/*.gds",
    "**/*.gds",
)
# Where the per-PDK antenna deck config may be declared.
_BRIDGE_CFG = "input/pdk/bridge/signoff_config.json"
_BRIDGE_KEY = "antenna_deck"
# The CONFIG filename is subject to the same collision as the report filenames
# (see the naming note in run()): `antenna_report_check` rglobs `*antenna*.json`
# across the WHOLE project and would ingest this deck config as if it were a
# router antenna report — it carries no violation count, so a project that
# adopted the deck would see spurious ANTENNA_VIOLATION_COUNT errors and, with
# no genuine antenna.rpt present, a hard FAIL of the sibling gate. Hence
# "gate_oxide", not "antenna".
_GEOMETRY_CFG_GLOBS = (
    "signoff/gate_oxide_deck.json",
    "input/pdk/bridge/gate_oxide_deck.json",
)
# The PDK-root receipt is emitted by the PDK bridge used by the real backend.
# It names both the selected PDK and the image-bound tree, so discovery cannot
# accidentally borrow an antenna deck from another installed PDK.
_PDK_ROOT_RECEIPT = "phase3/librelane_pdk_root.provenance.json"
_CFG_GLOBS = _GEOMETRY_CFG_GLOBS + (_PDK_ROOT_RECEIPT,)
_ROUTER_GLOBS = (
    "reports/phase3/antenna.rpt",
    "**/antenna*.rpt",
)
# See the naming note in run(): these must NOT match `*antenna*.json`.
#: Where an engine the runner cannot reach is STAGED to, relative to the
#: GDS directory. Must not contain the substring "antenna" for the same
#: reason the report names do not -- see the naming note in run().
_STAGE_REL = "gate_oxide_deck"
_RAW_REPORT_NAME = "gate_oxide_geom_deck_raw.json"
_MATERIALISED_CFG_NAME = "gate_oxide_geom_deck_config.json"


def _first(project: Path, globs) -> Optional[Path]:
    for g in globs:
        hits = sorted(p for p in project.glob(g) if p.is_file())
        if hits:
            return hits[0]
    return None


def _resolve_config(project: Path, explicit: Optional[str]) -> tuple:
    """Return (config_path, deck_dict, source) — deck_dict None when absent.

    A bridge `signoff_config.json` may declare `antenna_deck` either INLINE (a
    dict) or as a path relative to the PDK dir.
    """
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = project / p
        if not p.is_file():
            return None, None, f"config not found: {explicit}"
        return p, json.loads(p.read_text()), f"--config {explicit}"
    bridge = project / _BRIDGE_CFG
    if bridge.is_file():
        try:
            declared = json.loads(bridge.read_text()).get(_BRIDGE_KEY)
        except (ValueError, OSError, AttributeError) as exc:
            return None, None, f"{_BRIDGE_CFG} is unreadable: {exc}"
        if isinstance(declared, dict):
            return None, declared, f"{_BRIDGE_CFG}:{_BRIDGE_KEY} (inline)"
        if isinstance(declared, str):
            p = (bridge.parent / declared).resolve()
            if p.is_file():
                return p, json.loads(p.read_text()), \
                    f"{_BRIDGE_CFG}:{_BRIDGE_KEY} -> {declared}"
            return None, None, (f"{_BRIDGE_CFG}:{_BRIDGE_KEY} points at a "
                                f"missing deck: {declared}")
    found = _first(project, _GEOMETRY_CFG_GLOBS)
    if found:
        return found, json.loads(found.read_text()), \
            str(found.relative_to(project))
    return None, None, "no antenna deck config declared for this PDK"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _native_deck(project: Path):
    """Discover a PDK's own antenna rule and its runnable parent deck.

    The rule file is not an executable KLayout script on its own: the parent
    loads layers, options and the rule registry. Only a parent that declares
    the native ``decks`` selection grammar may be run with ``decks=antenna``.
    No PDK name, layer number or antenna ratio is supplied by this program.
    """
    receipt = project / _PDK_ROOT_RECEIPT
    if not receipt.is_file():
        return None, "no PDK-root bridge receipt to discover an antenna deck"
    try:
        doc = json.loads(receipt.read_text())
        root = Path(doc["path"])
        pdk = doc["derivation"]["pdk"]
        guest = doc["derivation"].get("guest_path")
        if not isinstance(pdk, str) or not pdk or "/" in pdk or pdk in (".", ".."):
            raise ValueError("invalid PDK identity")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f"PDK-root bridge receipt is unusable: {exc}"
    drc = root / pdk / "libs.tech" / "klayout" / "tech" / "drc"
    rule = drc / "rule_decks" / "antenna.rb"
    if not rule.is_file():
        return None, f"selected PDK has no native antenna rule deck: {rule}"
    parents = []
    for candidate in sorted(drc.glob("*.drc")):
        source = candidate.read_text(errors="replace")
        if "decks: $decks" in source and "rule_decks" in source:
            parents.append(candidate)
    if len(parents) != 1:
        return None, (f"native antenna rule exists, but exactly one runnable "
                      f"parent with deck selection was required; found {len(parents)}")
    return {"rule": rule, "parent": parents[0], "guest": guest,
            "receipt": receipt}, ""


_BIND_PREFIX = "VIBEIC_GDS_SHA256="
_NATIVE_RDB = "gate_oxide_native.lyrdb"
_NATIVE_LOG = "gate_oxide_native.log"


def _bound_native_count(gds: Path, rdb: Path, transcript: Path, gds_sha: str):
    """Read a fresh native result only while all three artefacts share a basis.

    The transcript is written by this invocation after its KLayout process
    exits. Its first line binds the process output to the pre-execution GDS
    hash; the caller also checks that the GDS did not change during execution.
    The returned hashes bind both tool outputs into the published gate report.
    """
    if not rdb.is_file() or not transcript.is_file() or _sha(gds) != gds_sha:
        return None, "GDS or native output missing/changed", {}
    log = transcript.read_text(errors="replace")
    if not log.startswith(_BIND_PREFIX + gds_sha + "\n"):
        return None, "native transcript is bound to another GDS SHA-256", {}
    if not re.search(r"Executing rule\s+ANT\.", log):
        return None, "native transcript names no executed ANT rule", {}
    import eda_report_audit as _audit  # existing calibrated KLayout RDB reader
    count = _audit._antenna_klayout_count(rdb.read_text(errors="replace"))
    if count is None:
        return None, "native RDB has no usable ANT measurement", {}
    return count, "", {"rdb_sha256": _sha(rdb),
                       "transcript_sha256": _sha(transcript)}


def _run_native(project: Path, gds: Optional[str], router: Optional[str],
                cell: Optional[str], native: dict) -> Dict[str, Any]:
    if gds:
        gds_path = Path(gds)
    else:
        # A directory with multiple streams does not identify the delivered
        # one. Sorting filenames is not a sign-off selection rule.
        staged = sorted((project / "phase3/stage4/gds").glob("*.gds"))
        if len(staged) > 1:
            return {"verdict": "DISCLOSED_SKIP", "check": "gds_geometry_antenna_deck",
                    "reason": "multiple streamed GDS files; select the delivered GDS with --gds"}
        gds_path = staged[0] if staged else _first(project, _GDS_GLOBS)
    if gds_path is not None and not gds_path.is_absolute():
        gds_path = project / gds_path
    if gds_path is None or not gds_path.is_file():
        return {"verdict": "DISCLOSED_SKIP", "check": "gds_geometry_antenna_deck",
                "reason": "no streamed GDS for the selected native PDK deck"}
    runner = _kl.find_runner(project=project)
    if runner is None or not runner.covers(gds_path):
        return {"verdict": "DISCLOSED_SKIP", "check": "gds_geometry_antenna_deck",
                "reason": "no KLayout runner reaches the streamed GDS"}
    work = project / "reports" / "phase3"
    work.mkdir(parents=True, exist_ok=True)
    rdb, transcript = work / _NATIVE_RDB, work / _NATIVE_LOG
    if not runner.covers(work):
        return {"verdict": "DISCLOSED_SKIP", "check": "gds_geometry_antenna_deck",
                "reason": "KLayout runner cannot write native antenna evidence"}
    for old in (rdb, transcript):
        old.unlink(missing_ok=True)
    before = _sha(gds_path)
    parent = native["parent"]
    if runner.kind == "container":
        guest = native.get("guest")
        if not isinstance(guest, str) or not guest:
            return {"verdict": "DISCLOSED_SKIP", "check": "gds_geometry_antenna_deck",
                    "reason": "PDK bridge has no container path for its native deck"}
        parent_arg = str(Path(guest) / parent.relative_to(parent.parents[4]))
        rule_arg = str(Path(guest) / native["rule"].relative_to(parent.parents[4]))
        rc_probe, out_probe, _ = runner.run_argv(
            ["sha256sum", parent_arg, rule_arg], {}, timeout=30)
        found = [line.split()[0] for line in out_probe.splitlines()]
        if rc_probe != 0 or found != [_sha(parent), _sha(native["rule"])]:
            return {"verdict": "DISCLOSED_SKIP", "check": "gds_geometry_antenna_deck",
                    "reason": "container PDK deck bytes differ from the image-bound bridge tree"}
    else:
        parent_arg = str(parent)
    argv = [runner.klayout_bin(), "-b", "-r", parent_arg,
            "-rd", f"input={runner.cpath(gds_path)}",
            "-rd", f"report={runner.cpath(rdb)}", "-rd", "decks=antenna"]
    if cell:
        argv.extend(["-rd", f"topcell={cell}"])
    rc, out, err = runner.run_argv(argv, {}, timeout=1800)
    _atomic_write_bytes(transcript, (_BIND_PREFIX + before + "\n" + out + err).encode())
    if rc != 0:
        return {"check": "gds_geometry_antenna_deck", "verdict": "FAIL",
                "method": "pdk_native_klayout", "rc": rc,
                "gds": str(gds_path), "gds_sha256": before,
                "pdk_rule": str(native["rule"]),
                "pdk_rule_sha256": _sha(native["rule"]),
                "transcript": str(transcript),
                "transcript_sha256": _sha(transcript),
                "reason": f"declared native KLayout deck failed rc={rc}"}
    count, why, hashes = _bound_native_count(gds_path, rdb, transcript, before)
    base = {"check": "gds_geometry_antenna_deck", "method": "pdk_native_klayout",
            "gds": str(gds_path), "gds_sha256": before,
            "pdk_rule": str(native["rule"]), "pdk_rule_sha256": _sha(native["rule"]),
            "pdk_parent": str(parent), "pdk_parent_sha256": _sha(parent),
            "rdb": str(rdb), "transcript": str(transcript), "rc": rc,
            **hashes}
    if why:
        return {**base, "verdict": "DISCLOSED_SKIP", "reason": why}
    raw = work / _RAW_REPORT_NAME
    _atomic_write_bytes(raw, json.dumps({"verdict": "PASS" if count == 0 else "FAIL",
                                         "violations": count}).encode())
    res = {**base, "verdict": "PASS" if count == 0 else "FAIL",
           "violations": count, "worst_ratio": None,
           "reason": f"{count} PDK-native antenna violation(s)" if count else ""}
    xtool = _kl.find_engine("gds_antenna", "xcheck_router.py")
    rpt = Path(router) if router else _first(project, _ROUTER_GLOBS)
    if rpt is not None and not rpt.is_absolute():
        rpt = project / rpt
    if xtool is not None and rpt is not None and rpt.is_file():
        try:
            x = _load_module(xtool, "_vibeic_xcheck_router").cross_check(raw, rpt)
        except Exception as exc:  # noqa: BLE001
            x = {"verdict": "ERROR", "detail": f"cross-check failed: {exc}"}
        res["cross_check"] = {**x, "router_report": str(rpt)}
        if x.get("verdict") == "DISAGREE":
            res.update(verdict="FAIL", reason=f"router-vs-geometry antenna DISAGREEMENT: {x.get('detail')}")
    else:
        res["cross_check"] = {"verdict": "NOT_RUN", "reason": "no router report or cross-check engine"}
    return res


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                             # type: ignore
    return mod


def stage_engine(runner, path: Path, into: Path):
    """`path` as the RUNNER can open it — a copy under `into` when it cannot.

    THE DEFECT THIS EXISTS FOR (vibe-ic#2119). `path` is a HOST path under this
    plugin's own installation, and the runner may be a CONTAINER. The plugin
    tree is not one of the mounts the repo's own container helper creates —
    `tools/vibeic-eda/restart-eda.sh` binds the designs directory and nothing
    else — so on a container built exactly the way this repo says to build one
    the host file exists, the container has no such path, and this gate
    DISCLOSED_SKIPped on every PDK and every design: the one INDEPENDENT
    antenna opinion in the flow never ran, and said so in a line nobody had to
    act on. After vibe-ic#2107 this was the ONLY one of the four KLayout-engine
    callers that did not stage.

    Copying is sound because the engine is self-contained: `antenna_check.py`
    imports only `json`, `os`, `sys` and `pya` and pulls in no sibling of its
    own, so a copy of it is the same program. Same remedy, and for the same
    measured reason, as `die_density_fill_gen.stage_engine`.

    Returns (path_for_the_runner, error_or_None). The error is NEVER folded
    into a skip or a pass: an engine the runner cannot open is this program
    failing, not the design failing and not the deck being undeclared.
    """
    if runner.covers(path):
        return path, None
    dest = into / path.name
    try:
        into.mkdir(parents=True, exist_ok=True)
        # ATOMIC, and not `dest.write_bytes` (vibe-ic#1082): a copy interrupted
        # halfway leaves a TRUNCATED engine at a path that exists, and KLayout
        # would then fail on a syntax error inside this program's own engine —
        # a failure indistinguishable from a defect in the engine itself.
        _atomic_write_bytes(dest, path.read_bytes())
    except OSError as exc:                                   # noqa: BLE001
        return path, (f"{path} is not reachable from the {runner.kind} KLayout "
                      f"environment ({runner.detail}) and could not be staged "
                      f"into {into}: {exc}")
    if not runner.covers(dest):
        return dest, (f"neither {path} nor a copy of it at {dest} is reachable "
                      f"from the {runner.kind} KLayout environment "
                      f"({runner.detail})")
    return dest, None


def run(project: Path, gds: Optional[str], config: Optional[str],
        router: Optional[str], cell: Optional[str]) -> Dict[str, Any]:
    """Return a verdict dict. Verdicts: PASS / FAIL / DISCLOSED_SKIP."""
    def skip(reason: str, **extra) -> Dict[str, Any]:
        return {"verdict": "DISCLOSED_SKIP", "reason": reason,
                "check": "gds_geometry_antenna_deck", **extra}

    cfg_path, deck, cfg_src = _resolve_config(project, config)
    if deck is None:
        if config or cfg_src != "no antenna deck config declared for this PDK":
            return skip(cfg_src, config_source=cfg_src)
        native, why = _native_deck(project)
        if native is None:
            return skip(f"{cfg_src}; {why}", config_source=cfg_src)
        return _run_native(project, gds, router, cell, native)

    engine = _kl.find_engine("gds_antenna", "antenna_check.py")
    if engine is None:
        return skip("GDS-geometry antenna engine not found "
                    "(gds_antenna/antenna_check.py missing; set "
                    "$VIBEIC_KLAYOUT_TOOLS to a KLayout-fork checkout)")

    gds_path = (Path(gds) if gds else _first(project, _GDS_GLOBS))
    if gds_path is not None and not gds_path.is_absolute():
        gds_path = project / gds_path
    if gds_path is None or not gds_path.is_file():
        return skip("no streamed GDS to check "
                    f"(looked for {', '.join(_GDS_GLOBS)})",
                    config_source=cfg_src)

    runner = _kl.find_runner(project=project)
    if runner is None:
        return skip("no KLayout runner available (no strmrun/klayout on PATH "
                    "and no KLayout in $VIBEIC_EDA_CONTAINER) — the antenna "
                    "geometry deck did NOT run",
                    config_source=cfg_src, gds=str(gds_path))

    work = project / "reports" / "phase3"
    work.mkdir(parents=True, exist_ok=True)
    # NAMING IS LOAD-BEARING: `antenna_report_check` (eda_report_audit --mode
    # antenna) rglobs `*antenna*.json` / `*antenna*.rpt` and treats every hit as
    # a ROUTER antenna report — it would swallow this gate's geometry JSON, find
    # no OpenROAD violation count in it and fail its own authenticity check. So
    # nothing this gate writes may contain the substring "antenna".
    out_json = work / _RAW_REPORT_NAME
    # An inline deck (or a config outside the runner's reach) is materialised
    # beside the output so the runner can always see it.
    if cfg_path is None or not runner.covers(cfg_path):
        cfg_path = work / _MATERIALISED_CFG_NAME
        cfg_path.write_text(json.dumps(deck, indent=2))
    for label, p in (("GDS", gds_path), ("report dir", work)):
        if not runner.covers(p):
            return skip(f"{label} path is not reachable by the KLayout runner "
                        f"({runner.kind}: {runner.detail}): {p}",
                        config_source=cfg_src)
    # NOW the engine can be checked on the side that will OPEN it, and staged
    # into the GDS directory — which the loop above has just proven the runner
    # reaches — when it is not reachable where it is. vibe-ic#2119.
    engine_staged = None
    _orig_engine = engine
    engine, _why = stage_engine(runner, engine, gds_path.parent / _STAGE_REL)
    if _why:
        return {"verdict": "FAIL", "check": "gds_geometry_antenna_deck",
                "config_source": cfg_src, "gds": str(gds_path),
                "runner": f"{runner.kind}:{runner.detail}",
                "reason": (f"this program's antenna geometry engine "
                           f"({_orig_engine.name}) is present on this host but "
                           f"cannot be opened where KLayout runs: {_why}")}
    if engine != _orig_engine:
        engine_staged = str(engine)

    env = {"ANT_GDS": str(gds_path), "ANT_CONFIG": str(cfg_path),
           "ANT_OUT": str(out_json)}
    if cell:
        env["ANT_CELL"] = cell
    if out_json.is_file():
        out_json.unlink()
    rc, out, err = runner.run(engine, env,
                              path_keys=("ANT_GDS", "ANT_CONFIG", "ANT_OUT"),
                              timeout=1800)
    if not out_json.is_file():
        return {"verdict": "FAIL", "check": "gds_geometry_antenna_deck",
                "reason": "antenna geometry deck produced no report",
                "config_source": cfg_src, "gds": str(gds_path),
                "engine_staged": engine_staged,
                "runner": f"{runner.kind}:{runner.detail}", "rc": rc,
                "stderr": (err or "")[-600:], "stdout": (out or "")[-600:]}
    deck_res = json.loads(out_json.read_text())

    res: Dict[str, Any] = {
        "check": "gds_geometry_antenna_deck",
        "runner": f"{runner.kind}:{runner.detail}",
        "config_source": cfg_src, "gds": str(gds_path),
        "engine_staged": engine_staged,
        "worst_ratio": deck_res.get("worst_ratio"),
        "violations": deck_res.get("violations"),
        "deck": deck_res,
    }
    dv = deck_res.get("verdict")
    if dv == "PASS":
        res["verdict"] = "PASS"
    elif dv == "FAIL":
        res["verdict"] = "FAIL"
        res["reason"] = (f"{deck_res.get('violations')} antenna violation(s) in "
                         f"the GDS geometry; worst ratio "
                         f"{deck_res.get('worst_ratio')}")
    else:
        # HONEST_SKIP / ERROR from the engine: the deck was DECLARED but did not
        # produce a usable measurement. Never a pass — see module docstring.
        res["verdict"] = "FAIL"
        res["reason"] = ("antenna deck ran but produced no usable measurement "
                         f"({dv}): {deck_res.get('note') or deck_res.get('error')}"
                         " — a declared deck that cannot measure is not clean")

    # Independent cross-check against the router's own count.
    xtool = _kl.find_engine("gds_antenna", "xcheck_router.py")
    rpt = Path(router) if router else _first(project, _ROUTER_GLOBS)
    if rpt is not None and not rpt.is_absolute():
        rpt = project / rpt
    if xtool is not None and rpt is not None and rpt.is_file():
        try:
            xr = _load_module(xtool, "_vibeic_xcheck_router")
            x = xr.cross_check(out_json, rpt)                # type: ignore
        except Exception as exc:                             # noqa: BLE001
            x = {"verdict": "ERROR", "detail": f"cross-check failed: {exc}"}
        x["router_report"] = str(rpt)
        res["cross_check"] = x
        if x.get("verdict") == "DISAGREE":
            res["verdict"] = "FAIL"
            res["reason"] = (
                "router-vs-geometry antenna DISAGREEMENT — one of the two is "
                f"wrong: {x.get('detail')}")
    else:
        res["cross_check"] = {
            "verdict": "NOT_RUN",
            "reason": ("no router antenna report to cross-check against"
                       if rpt is None or not rpt.is_file()
                       else "xcheck_router.py engine not found")}
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Independent GDS-geometry process-antenna sign-off gate.")
    ap.add_argument("project_dir", nargs="?", default=".")
    ap.add_argument("--gds", default=None)
    ap.add_argument("--config", default=None,
                    help="antenna deck config (per-PDK layer stack + ratios)")
    ap.add_argument("--router", default=None,
                    help="router antenna report to cross-check against")
    ap.add_argument("--cell", default=None)
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--strict", action="store_true",
                    help="treat a disclosed skip as a FAIL (tapeout sign-off)")
    ns = ap.parse_args(argv)

    project = Path(ns.project_dir).resolve()
    try:
        res = run(project, ns.gds, ns.config, ns.router, ns.cell)
    except Exception as exc:                                 # noqa: BLE001
        res = {"verdict": "FAIL", "check": "gds_geometry_antenna_deck",
               "reason": f"gate error: {exc}"}

    if ns.json_out:
        out = Path(ns.json_out)
        if not out.is_absolute():
            out = project / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=2))

    verdict = res.get("verdict")
    if verdict == "DISCLOSED_SKIP" and ns.strict:
        verdict = "FAIL"
        res["reason"] = f"--strict: {res.get('reason')}"
    if verdict == "DISCLOSED_SKIP":
        # Plugin disclosed-skip tier: rc 2 + sentinel -> VACUOUS-PASS, so the
        # flow report SHOWS the checker did not run instead of reading clean.
        print(f"VACUOUS_PASS: gds_antenna_deck_check did NOT run — "
              f"{res.get('reason')}")
        print(json.dumps(res, indent=2))
        return SKIP
    print(json.dumps(res, indent=2))
    if verdict == "PASS":
        if res.get("method") == "pdk_native_klayout":
            print("gds_antenna_deck_check: PASS "
                  "(0 PDK-native antenna violations on the bound GDS)")
        else:
            print("gds_antenna_deck_check: PASS "
                  f"(0 geometry antenna violations, worst ratio "
                  f"{res.get('worst_ratio')})")
        return PASS
    print(f"gds_antenna_deck_check: FAIL — {res.get('reason')}")
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
