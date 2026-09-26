#!/usr/bin/env python3
"""consistency_tier.py — bookkeeping tests run at a milestone or on request, never routinely.

THE OWNER'S RULING (2026-09-26)
===============================
Pure bookkeeping tests — the ones whose subject is that INFORMATION matches the
code — are needed only at a big version change (x.y.99 -> x.(y+1).0, the same
moment `gatekeeper_review.derive_cadence` answers FULL), or when the owner
explicitly asks for a full test. They must not be routine, and nothing is
deleted: their purpose is to keep what we SAY (counts, lists, inventories,
indexes, registers, stated figures, file order, the website) consistent with
what the codebase IS.

A test belongs here only when what it checks is information-vs-code:

    stated counts, lists, inventories and indexes
    member pins of derived sets
    registers and ledgers that must equal a measured set
    file or step ORDER
    numbers or names stated in READMEs, docs or website pages

A test of how the flow, a gate, a tool or a program BEHAVES is not one of
these, even when it also counts something. Such a test carries no mark.

THE MECHANISM: DESELECTED, NOT SKIPPED
======================================
A test marked `@pytest.mark.consistency` is DESELECTED at collection unless
`VIBEIC_RUN_CONSISTENCY=1`. Deselected, not skipped: a skip is still a counted
outcome in the summary and the JUnit, and the ruling is that these tests are
neither run nor counted routinely. Every session that deselects some says so
on one line, with the number, so their absence is disclosed rather than silent.

The marker is registered here, so no session warns about an unknown mark.

This module is loaded as a pytest plugin by the plugin-root `conftest.py`
(beside `suite_write_guard`) and by the repo-level `tools/conftest.py`.

WHO SETS THE VARIABLE
=====================
    tools/gatekeeper-land.sh   exports it when LANDING_CADENCE is FULL (the
                               x.y.0 milestone, derived from the tree by
                               `landing_cadence.py` -> `derive_cadence`), and
                               clears it otherwise. There is no flag.
    the owner, on request      VIBEIC_RUN_CONSISTENCY=1 bash run_tests.sh
                               (the full suite, every tier, consistency included)
                               or `python3 programs/consistency_tier.py --resync`
                               (only the consistency tier, then the resync
                               report below).

THE RESYNC (``--resync``)
=========================
A consistency run is a resync, not only a verdict. ``--resync``:

  1. runs every consistency test (all plugin tiers + repo tools/), with the
     variable set and ``-m consistency``;
  2. with ``--apply``, runs the REGENERATORS below — the facts a program can
     re-derive (inventory, index, figures). Without ``--apply`` it only names
     them: regenerating writes the tree, and that is the operator's decision;
  3. lists what must be edited BY HAND — every red consistency test whose file
     has no regenerator;
  4. lists the website (vibeic/vibeic.ai) facts that state codebase facts,
     with where each one comes from, so that page can be brought back in line.
     With ``--website-repo PATH`` each declared anchor is looked up and the
     stated line is printed beside its source.

Exit: 0 every consistency test green; 1 at least one red (the resync report
says what to do about each); 2 the run could not be measured.

chip-AGNOSTIC / PDK-AGNOSTIC / tool-AGNOSTIC: pytest and repository bookkeeping only.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ENV = "VIBEIC_RUN_CONSISTENCY"
MARKER = "consistency"
_MARKER_LINE = (f"{MARKER}: pure bookkeeping — information (counts, lists, "
                f"registers, order, stated figures) must match the code. "
                f"DESELECTED unless {ENV}=1 (x.y.0 FULL cadence or owner request).")

_PROGRAMS = Path(__file__).resolve().parent
_PLUGIN = _PROGRAMS.parent
_REPO = _PLUGIN.parents[2]


def enabled(environ=None) -> bool:
    """True only for the exact value ``1`` — any other spelling is routine."""
    env = os.environ if environ is None else environ
    return env.get(ENV, "") == "1"


# ── pytest plugin ─────────────────────────────────────────────────────────────
_DESELECTED_KEY = "_consistency_tier_deselected"


def pytest_configure(config):
    config.addinivalue_line("markers", _MARKER_LINE)


def split_items(items, include: bool):
    """(kept, deselected). Pure, so it is testable without a session."""
    if include:
        return list(items), []
    kept, dropped = [], []
    for item in items:
        (dropped if item.get_closest_marker(MARKER) is not None else kept).append(item)
    return kept, dropped


def pytest_collection_modifyitems(config, items):
    # ACCUMULATES, because the same hooks can be registered twice in one session
    # (the plugin-root conftest AND `tools/conftest.py`, when one invocation
    # spans both trees). The second call finds nothing left to drop and must not
    # erase the first call's count.
    kept, dropped = split_items(items, enabled())
    setattr(config, _DESELECTED_KEY,
            getattr(config, _DESELECTED_KEY, 0) + len(dropped))
    if dropped:
        items[:] = kept
        config.hook.pytest_deselected(items=dropped)


_REPORTED_KEY = "_consistency_tier_reported"


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    if getattr(config, _REPORTED_KEY, False):
        return
    setattr(config, _REPORTED_KEY, True)
    n = getattr(config, _DESELECTED_KEY, 0)
    if n:
        terminalreporter.write_line(
            f"consistency tier: {n} bookkeeping test(s) DESELECTED — not run, not "
            f"counted. They run at the x.y.0 FULL cadence or with {ENV}=1.")
    elif enabled():
        terminalreporter.write_line(
            f"consistency tier: INCLUDED ({ENV}=1).")


# ── the resync ────────────────────────────────────────────────────────────────
#: Test file (plugin- or repo-relative) -> the command that re-derives what it
#: checks. Run from the plugin root unless the command starts with "tools/".
#: A red consistency test whose file is not here is a HAND edit.
REGENERATORS: Dict[str, Tuple[str, ...]] = {
    "programs/tests/test_program_inventory_no_drift.py":
        ("python3", "programs/gen_program_inventory.py"),
    "programs/tests/test_programs_index_freshness.py":
        ("python3", "tools/gen_programs_index.py"),
}

def _json(rel: str) -> dict:
    import json
    return json.loads((_PLUGIN / rel).read_text(encoding="utf-8"))


def _flow() -> dict:
    import yaml  # the flow's own format; PyYAML is already a suite dependency
    return yaml.safe_load((_PLUGIN / "flow" / "phase1_phase2_phase3.yaml")
                          .read_text(encoding="utf-8"))


def _dimensions() -> int:
    import ast
    tree = ast.parse((_PROGRAMS / "flow_gate_grid.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", "") == "DIMENSIONS"):
            return len(ast.literal_eval(node.value))
    raise LookupError("flow_gate_grid.DIMENSIONS not found")


#: The source of truth for each fact, READ FROM THE TREE on every call — the
#: website is compared with these, never with a number typed here.
LIVE: Dict[str, Tuple[str, object]] = {
    "plugin_version": (".claude-plugin/plugin.json .version",
                       lambda: _json(".claude-plugin/plugin.json")["version"]),
    "skills_total": ("SKILL_INVENTORY.json .total (gen_skill_inventory.py)",
                     lambda: _json("SKILL_INVENTORY.json")["total"]),
    "mcp_tools": ("mcp-eda/MCP_TOOL_INVENTORY.json .total/.by_category "
                  "(mcp-eda/tools/gen_mcp_tool_inventory.py)",
                  lambda: "{total} = {by_category}".format(
                      **_json("mcp-eda/MCP_TOOL_INVENTORY.json"))),
    "programs": ("programs/PROGRAM_INVENTORY.json .populations "
                 "(gen_program_inventory.py) — name the population, there are several",
                 lambda: {k: v["count"] for k, v in
                          _json("programs/PROGRAM_INVENTORY.json")["populations"].items()
                          if k in ("programs_catalogued", "programs_top_level",
                                   "gate_programs_check_audit_lint", "test_files")}),
    "flow_steps": ("flow/phase1_phase2_phase3.yaml len(steps) / total_steps / stages",
                   lambda: {"step_entries": len(_flow()["steps"]),
                            "total_steps": _flow().get("total_steps"),
                            "analog_steps": _flow().get("analog_steps"),
                            "stages": len(_flow().get("stages") or {})}),
    "gate_matrix": ("programs/flow_gate_grid.py DIMENSIONS x flow step entries",
                    lambda: "{} dimensions x {} steps = {} cells".format(
                        _dimensions(), len(_flow()["steps"]),
                        _dimensions() * len(_flow()["steps"]))),
    "slash_commands": ("commands/vibe-ic-*.md",
                       lambda: sorted(p.stem for p in (_PLUGIN / "commands")
                                      .glob("vibe-ic-*.md"))),
    "pdks": ("programs/pdk_registry.json .pdks",
             lambda: sorted(e.get("name", "?") for e in
                            _json("programs/pdk_registry.json").get("pdks", []))),
    "collected_tests": ("pytest --collect-only (no committed count exists)", None),
    "eda_image": ("vibeic/vibeic-eda releases; generated eda-forks.html "
                  "(fork-gatekeeper/build_page.py)", None),
    "forks": ("vibeic/vibeic-eda FORKS.json; generated eda-forks.html", None),
    "ics_validated": ("vibeic/benchmark-data (generated IC_PDK_MATRIX block, "
                      "_gen/build_ic_matrix.py)", None),
}

#: The website repository's (vibeic/vibeic.ai) statements of codebase facts,
#: surveyed 2026-09-26 at site 94b50d0 against plugin 40315b3aa. Each row: the
#: page, a regex that finds the statement, what it states, and the LIVE key
#: whose source is the truth. The website is NOT edited from here; this list is
#: what a FULL/requested run hands to whoever brings that page back in line.
#: Generated regions (IC_PDK_MATRIX blocks, eda-forks.html) are regenerated by
#: their own generators and are listed for completeness only.
WEBSITE_FACTS: Tuple[Tuple[str, str, str, str], ...] = (
    ("index.html", r'"softwareVersion"', "JSON-LD softwareVersion", "plugin_version"),
    ("index.html", r"60 AI Skills", "skill count (also :102)", "skills_total"),
    ("index.html", r"56 tools", "MCP tool count, 48 EDA + 7 device + 1 health", "mcp_tools"),
    ("index.html", r"918", "program count (also :324, :370, :388)", "programs"),
    ("index.html", r"17,545", "collected test count", "collected_tests"),
    ("index.html", r"63-step flow", "flow step count", "flow_steps"),
    ("index.html", r"55-entity flow YAML", "flow step entries", "flow_steps"),
    ("index.html", r"21 tracked tool forks", "fork count", "forks"),
    ("index.html", r"vibeic-eda:0\.3\.", "docker pull image tag", "eda_image"),
    ("index.html", r"8 ICs", "ICs sign-off validated (also :105)", "ics_validated"),
    ("manual.html", r"v1\.9\.36", "plugin version snapshot", "plugin_version"),
    ("manual.html", r"24 forks", "fork count", "forks"),
    ("manual.html", r"Five slash commands", "slash command count and list", "slash_commands"),
    ("manual.html", r"68 step entries", "step entries and judgment dimensions", "gate_matrix"),
    ("flow-blueprint.html", r"68 step entries|68 flow steps", "step entries, stages, dimensions (meta)", "gate_matrix"),
    ("flow-blueprint.html", r"63 . 8 = 504", "gate matrix cells", "gate_matrix"),
    ("flow-blueprint.html", r"five siblings", "one-shot runner count", "flow_steps"),
    ("flow.html", r"70-step", "step count (currently correct)", "flow_steps"),
    ("flow.html", r"630", "9 dimensions / 630 cells (currently correct)", "gate_matrix"),
    ("videos.html", r"612 cells|68 . 9", "gate matrix cells", "gate_matrix"),
    ("platform.html", r"vibe-ic v1\.4\.67", "plugin version", "plugin_version"),
    ("platform.html", r"60 skills", "skill and program counts", "skills_total"),
    ("platform.html", r"48 EDA", "MCP tool split (currently correct)", "mcp_tools"),
    ("terms.html", r"60 Claude Code Plugin Skills", "skill count", "skills_total"),
    ("terms.html", r"918 deterministic", "program and test counts", "programs"),
    ("terms.html", r"56 MCP", "MCP tool count (currently correct)", "mcp_tools"),
    ("ppa.html", r"68 flow steps", "flow steps / closed-loop edges", "flow_steps"),
    ("evaluation.html", r"8 real ICs", "ICs driven end-to-end", "ics_validated"),
    ("README.md", r"^# .*v1\.0\.0|v1\.0\.0", "site README version", "plugin_version"),
    ("README.md", r"\| *58", "skill count table", "skills_total"),
    ("README.md", r"55 = 47", "MCP tool count", "mcp_tools"),
    ("README.md", r"673", "program count", "programs"),
    ("README.md", r"9331", "test count", "collected_tests"),
    ("README.md", r"7-13|14-30", "stage step ranges", "flow_steps"),
)


def _tiers() -> List[str]:
    """The full suite's tiers, asked of run_tests.sh — never a second roster.

    Asked through `full_suite_run_check.runner_tiers`, the one interrogation
    of a runner's `--list-tiers` this tree has. It launches the script under
    `_watchdog.run_supervised`; a bare `subprocess.run(["bash", <script>])` is
    an opaque shell runner that `loop_watchdog_compliance_check` rightly
    refuses, since no AST pass can see what the script launches.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "full_suite_run_check", _PROGRAMS / "full_suite_run_check.py")
    fsrc = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("full_suite_run_check", fsrc)  # its @dataclass needs it
    spec.loader.exec_module(fsrc)
    tiers = fsrc.runner_tiers(_PLUGIN / "run_tests.sh")
    if not tiers:
        raise RuntimeError("run_tests.sh --list-tiers gave no tiers (it failed, "
                           "stalled, or named something that is not a directory)")
    return tiers


def _junit_results(xml_path: Path) -> Dict[str, str]:
    """nodeid -> PASSED/FAILED/ERROR/SKIPPED, read from pytest's own JUnit.

    NOT from the `-rA` summary text: a parametrized id may contain spaces, and
    a `\\S+` scrape of that text silently merges such ids into one key —
    measured, 111 results for 124 collected nodes. `junit_family=xunit1`
    carries the `file` attribute, so the nodeid is rebuilt from pytest's record.
    """
    import xml.etree.ElementTree as ET
    out: Dict[str, str] = {}
    root = ET.parse(str(xml_path)).getroot()
    for case in root.iter("testcase"):
        f = case.get("file") or ""
        cls = (case.get("classname") or "").split(".")[-1]
        stem = Path(f).stem
        parts = [f] + ([cls] if cls and cls != stem else []) + [case.get("name", "")]
        tags = {child.tag for child in case}
        state = ("FAILED" if "failure" in tags else "ERROR" if "error" in tags
                 else "SKIPPED" if "skipped" in tags else "PASSED")
        out["::".join(parts)] = state
    return out


def _run_consistency(cwd: Path, targets: Sequence[str],
                     extra: Sequence[str] = ()) -> Tuple[int, Dict[str, str], str]:
    import tempfile
    env = dict(os.environ)
    env[ENV] = "1"
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    with tempfile.TemporaryDirectory(prefix="consistency_tier_") as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               "-o", "junit_family=xunit1", f"--junitxml={junit}",
               "-m", MARKER, *extra, *targets]
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True,
                              text=True)
        results = _junit_results(junit) if junit.is_file() else {}
    return proc.returncode, results, proc.stdout + proc.stderr


def _tools_test_files() -> List[str]:
    out = []
    for p in sorted((_REPO / "tools").rglob("*.py")):
        rel = p.relative_to(_REPO).as_posix()
        if rel.startswith("tools/harvest/"):
            continue
        if p.name.startswith("test_") or p.name.endswith("_test.py"):
            out.append(rel)
    return out


def _file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def website_report(site: Path | None) -> List[str]:
    lines = ["WEBSITE (vibeic/vibeic.ai) — codebase facts it states; edit there, not here:"]
    if not WEBSITE_FACTS:
        lines.append("  (no facts declared)")
    for page, anchor, fact, key in WEBSITE_FACTS:
        where, derive = LIVE[key]
        try:
            live = derive() if derive else "not derivable in this repository"
        except Exception as exc:  # a source that cannot be read is named, never guessed
            live = f"UNREADABLE ({exc.__class__.__name__}: {exc})"
        source = f"{where}  ->  live: {live}"
        stated = ""
        if site is not None:
            f = site / page
            if f.is_file():
                rx = re.compile(anchor)
                for i, ln in enumerate(f.read_text(encoding="utf-8",
                                                   errors="replace").splitlines(), 1):
                    if rx.search(ln):
                        stated = f"  [{page}:{i}] {ln.strip()[:120]}"
                        break
                else:
                    stated = f"  [{page}] ANCHOR NOT FOUND — the statement moved or was removed"
            else:
                stated = f"  [{page}] PAGE NOT FOUND"
        lines.append(f"  - {page}: {fact}\n      source: {source}" + (f"\n    {stated}" if stated else ""))
    return lines


def resync(apply: bool, site: Path | None) -> int:
    try:
        tiers = _tiers()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"NOT_MEASURED: {exc}")
        return 2
    rc_p, res_p, out_p = _run_consistency(_PLUGIN, tiers)
    tools = _tools_test_files()
    rc_t, res_t, out_t = (_run_consistency(_REPO, tools) if tools else (5, {}, ""))
    results = dict(res_p)
    results.update(res_t)
    for rc, out, where in ((rc_p, out_p, "plugin tiers"), (rc_t, out_t, "repo tools/")):
        if rc not in (0, 1, 5):
            print(f"NOT_MEASURED: the {where} consistency session exited {rc}:\n{out[-3000:]}")
            return 2
    if not results:
        print("NOT_MEASURED: no consistency test was collected in any tier — "
              "an empty run is not a green one.")
        return 2
    red = sorted(n for n, s in results.items() if s in ("FAILED", "ERROR"))
    skipped = sorted(n for n, s in results.items() if s == "SKIPPED")
    passed = len(results) - len(red) - len(skipped)
    print(f"CONSISTENCY RUN: {len(results)} test(s), {passed} passed, "
          f"{len(red)} red, {len(skipped)} skipped")
    for n in red:
        print(f"  RED {n}")
    for n in skipped:
        print(f"  NOT MEASURED (skipped) {n}")
    regen_files = sorted({_file_of(n) for n in red if _file_of(n) in REGENERATORS})
    hand = sorted(n for n in red if _file_of(n) not in REGENERATORS)
    print("\nREGENERATE (a program re-derives these):")
    for f in sorted(REGENERATORS):
        mark = "RED " if f in regen_files else "ok  "
        print(f"  {mark}{f}\n      -> {' '.join(REGENERATORS[f])}")
    if apply:
        for f in regen_files:
            cmd = list(REGENERATORS[f])
            cwd = _REPO if cmd[1].startswith("tools/") else _PLUGIN
            p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
            print(f"  APPLIED rc={p.returncode}: {' '.join(cmd)}")
    elif regen_files:
        print("  (not applied — re-run with --apply to write the regenerated files)")
    print("\nEDIT BY HAND (red, and no program re-derives it):")
    for n in hand:
        print(f"  {n}")
    if not hand:
        print("  (none)")
    print()
    print("\n".join(website_report(site)))
    return 1 if red else 0


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--resync", action="store_true",
                    help="run the consistency tier, then report regenerate / "
                         "hand-edit / website lists")
    ap.add_argument("--apply", action="store_true",
                    help="with --resync: run the regenerators for red files")
    ap.add_argument("--website-repo", type=Path, default=None,
                    help="a vibeic.ai checkout; print each stated fact's line")
    ap.add_argument("--website", action="store_true",
                    help="print only the website fact list")
    args = ap.parse_args(argv)
    if args.website:
        print("\n".join(website_report(args.website_repo)))
        return 0
    if args.resync:
        return resync(args.apply, args.website_repo)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
