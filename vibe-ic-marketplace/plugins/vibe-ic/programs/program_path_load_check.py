#!/usr/bin/env python3
"""program_path_load_check.py — every program must import when it is loaded
BY PATH, not only when it is run as a script.

WHY (vibe-ic#2104)
------------------
`programs/` is a flat directory whose modules import each other by BARE name
(`from _path_layout import ...`). That resolves for exactly one reason: when
Python runs a file as `__main__` it puts that file's directory on `sys.path`.
`importlib.util.spec_from_file_location` — how the gates, the wiring audit and
much of the suite load a program — does NOT, so a bare sibling import raises
`ModuleNotFoundError` and whatever the caller was measuring is measured
against a module that never loaded.

It is worse than a crash where the import is wrapped in `try: ... except
ImportError:` with a degraded fallback. `lec_equivalence_check` did exactly
that: loaded by path it substituted a stub returning `None` for the structural
port-abort classifier, and went on emitting the FALSE diagnosis the classifier
exists to replace, with nothing in the output saying the classifier was
absent. A quiet wrong answer, not an error.

The failure is also ORDER-DEPENDENT, which is why it stayed latent: any
program loaded earlier in the same process that does put `programs/` on
`sys.path` fixes it for everything loaded afterwards. Whether a program
imports depends on who ran before it.

MEASURED on 94617408759e: 470 of the 1385 top-level programs failed this way,
by two independent methods that agreed exactly — one subprocess per file, and
the in-process sweep this program performs.

WHAT IT CHECKS
--------------
Each `programs/*.py` is loaded by `spec_from_file_location` IN ITS OWN CHILD
INTERPRETER, with the programs directory removed from that child's `sys.path`.
A `ModuleNotFoundError` naming ANOTHER PROGRAM in this directory (module or
package) is the offence.

ONE CHILD PER FILE, and that is not caution — the in-process version of this
sweep DIED. It snapshotted `sys.path` and `sys.modules` around each load, which
is correct as far as it goes, and it was measured green on the host. Run inside
the pinned EDA image it exited 139 — SIGSEGV — partway through the directory,
wrote no report, and the test that consumed the report failed while reading a
file that was never created. Some program in this tree imports a native
extension that does not survive being loaded beside 1388 others in one
interpreter. A per-file child cannot be killed by a file it has not reached, it
cannot be helped by one it has already loaded, and when a file does kill its
interpreter the gate NAMES it instead of dying with it. Verified in the same
image, one child per file: no program crashes on its own.

A failure that names something else — an absent third-party package, a syntax
error, an import-time exception — is NOT this program's subject. Those are
reported separately, by name and by message, and do not decide the exit code:
a missing `jsonschema` on the host is a fact about the host, and reporting it
here would be this gate answering a question it did not ask.

A child that dies by a signal, or returns no verdict at all, is UNMEASURED. It
is printed by name and it exits 2 — "I could not look" is never reported as
"I looked and it was clean".

BLOCKING. The population is 0 on a healthy tree, so any offender is a
regression and the exit code says so. It runs no tool and enters no container.

chip-AGNOSTIC: the subject is the import graph of this directory. No design,
PDK or vendor literal participates.

USAGE
-----
    program_path_load_check.py [--programs DIR] [--json OUT]

EXIT CODES
----------
    0 = every program loaded by path
    1 = at least one program could not resolve a SIBLING by path
    2 = something could not be measured (the directory, or a child that died)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Tuple

GATE = "program_path_load_check"

#: `No module named 'x'` / `No module named 'x.y'`, whichever spelling the
#: interpreter used. Read from the exception's own `name` first; this is the
#: fallback for the older message-only shape.
_MISSING_RE = re.compile(r"No module named '([^']+)'")


def sibling_names(programs: Path) -> set:
    """Every module name importable from this directory by a bare name.

    PACKAGES count, not only modules: `programs/_ppa/` is a package and
    `ppa_area_threshold_check` imports `_ppa` by bare name, so a `*.py`-only
    answer would file that program under "not this gate's subject" and quietly
    shrink the population the gate is responsible for.
    """
    names = {p.stem for p in programs.glob("*.py")}
    names |= {d.name for d in programs.iterdir()
              if d.is_dir() and (d / "__init__.py").is_file()}
    return names


def _missing_module(exc: ModuleNotFoundError) -> str:
    name = getattr(exc, "name", None)
    if name:
        return str(name)
    m = _MISSING_RE.search(str(exc))
    return m.group(1) if m else ""


def load_isolated(path: Path, programs: Path) -> Tuple[str, str, str]:
    """`(verdict, kind, detail)` for ONE program, IN THIS interpreter.

    verdict is "ok", "sibling-unresolved" or "other". This is the CHILD half —
    `sweep` never calls it in the parent. It still restores `sys.path` and
    `sys.modules` so it is usable directly in a test, but the isolation the
    sweep relies on is the process boundary, not this restore.
    """
    saved_path, saved_mods = list(sys.path), set(sys.modules)
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            return "other", "NoLoader", "importlib produced no loader"
        mod = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = mod
        spec.loader.exec_module(mod)
    except ModuleNotFoundError as exc:
        missing = _missing_module(exc)
        root = missing.split(".")[0]
        if root and root in sibling_names(programs) and root != path.stem:
            return "sibling-unresolved", root, str(exc)
        return "other", type(exc).__name__, str(exc)
    except BaseException as exc:            # noqa: BLE001 - see the docstring
        return "other", type(exc).__name__, str(exc)
    finally:
        sys.path[:] = saved_path
        for key in set(sys.modules) - saved_mods:
            del sys.modules[key]
    return "ok", "", ""


#: The child prints exactly one line beginning with this, and nothing the
#: program under test writes to stdout can be mistaken for it.
_VERDICT_PREFIX = "__program_path_load_check__ "


def _child(target: str, programs: str) -> int:
    """`--one` mode: load ONE program, print one verdict line, exit 0.

    BOTH directories come off `sys.path`, and the second one is why this gate
    can fail at all. Measured while writing it: with only the tree under test
    removed, the sweep reported PASS on a base tree where two independent
    sweeps had just measured 470 failures — Python had put THIS FILE's
    directory, the repaired `programs/`, on `sys.path[0]`, and every bare
    import in the tree under test resolved against the repaired copy. A gate
    that answers PASS on a tree known to be broken is a second copy of the
    defect.
    """
    prog_dir = Path(programs).resolve()
    blocked = {str(prog_dir), str(Path(__file__).resolve().parent)}
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p or ".") not in blocked]
    verdict, kind, detail = load_isolated(Path(target), prog_dir)
    sys.stdout.flush()
    print(_VERDICT_PREFIX + json.dumps(
        {"verdict": verdict, "kind": kind, "detail": detail}))
    return 0


def _read_verdict(stdout: str):
    """The child's verdict line, or None when it never printed one.

    Read from the LAST matching line: a program that prints at import time —
    several in this tree do — puts its own text on the same stream.
    """
    found = None
    for line in stdout.splitlines():
        if line.startswith(_VERDICT_PREFIX):
            found = line[len(_VERDICT_PREFIX):]
    if found is None:
        return None
    try:
        return json.loads(found)
    except ValueError:
        return None


def sweep(programs: Path, jobs: int = 1) -> Dict[str, list]:
    """Load every `programs/*.py` by path, ONE CHILD INTERPRETER PER FILE."""
    files = sorted(programs.glob("*.py"))
    offenders: List[dict] = []
    other: List[dict] = []
    unmeasured: List[dict] = []

    def run_one(p: Path) -> tuple:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "--one", str(p), "--programs", str(programs)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True)
        return p, proc

    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(run_one, files))
    else:
        results = [run_one(p) for p in files]

    for p, proc in results:
        doc = _read_verdict(proc.stdout)
        if doc is None:
            why = (f"killed by signal {-proc.returncode}"
                   if proc.returncode < 0
                   else f"exited {proc.returncode} with no verdict")
            unmeasured.append({"program": p.name, "reason": why,
                               "stderr": proc.stderr[-400:]})
            continue
        if doc["verdict"] == "sibling-unresolved":
            offenders.append({"program": p.name,
                              "unresolved_sibling": doc["kind"],
                              "error": doc["detail"]})
        elif doc["verdict"] == "other":
            other.append({"program": p.name, "error_type": doc["kind"],
                          "error": doc["detail"]})
    return {"measured": len(files), "offenders": offenders, "other": other,
            "unmeasured": unmeasured}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--programs", default=str(Path(__file__).resolve().parent),
                    help="the programs directory to sweep")
    ap.add_argument("--one", help="CHILD MODE: load this one file and report")
    ap.add_argument("--jobs", type=int, default=1,
                    help="child interpreters to run at once")
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args(argv)

    if a.one:
        return _child(a.one, a.programs)

    programs = Path(a.programs)
    if not programs.is_dir():
        print(f"[FAIL] {GATE}: not a directory: {programs}. NOTHING WAS "
              f"MEASURED — this is not a statement about any tree.")
        return 2

    result = sweep(programs, jobs=max(1, a.jobs))
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(result, indent=2))

    print(f"{GATE}: loaded {result['measured']} program(s) by path from "
          f"{programs}")
    for row in result["other"]:
        print(f"  [not this gate's subject] {row['program']}: "
              f"{row['error_type']}: {row['error']}")
    for row in result["unmeasured"]:
        print(f"  [NOT_MEASURED] {row['program']}: {row['reason']}")
    if result["unmeasured"]:
        print(f"[FAIL] {GATE}: {len(result['unmeasured'])} program(s) could "
              f"not be measured — their child interpreter did not report. "
              f"That is not a clean result; it is no result.")
        return 2
    if not result["offenders"]:
        print(f"[PASS] {GATE}: every program resolves its siblings when loaded "
              f"by `spec_from_file_location`.")
        return 0
    for row in result["offenders"]:
        print(f"  [FAIL] {row['program']} cannot resolve sibling "
              f"`{row['unresolved_sibling']}`: {row['error']}")
    print(f"[FAIL] {GATE}: {len(result['offenders'])} of {result['measured']} "
          f"program(s) raise ModuleNotFoundError for a SIBLING when loaded by "
          f"path. Put the program's own directory on `sys.path` before the "
          f"bare import (see vibe-ic#2104); do not silence the import.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
