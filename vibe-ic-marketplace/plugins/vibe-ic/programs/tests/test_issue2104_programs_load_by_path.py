"""vibe-ic#2104 — every program must import when it is loaded BY PATH.

`programs/` is a flat directory whose modules import each other by BARE name.
That resolves for one reason only: running a file as `__main__` puts its
directory on `sys.path`. `importlib.util.spec_from_file_location` does not, so
a bare sibling import raises `ModuleNotFoundError` and the caller measures a
module that never loaded.

MEASURED on 94617408759e by two independent methods that agreed exactly — one
subprocess per file, and the in-process sweep `program_path_load_check`
performs — 470 of the 1385 top-level programs failed: 454 with an unresolved
SIBLING, and 16 more whose `except ImportError:` fell through to a relative
import that cannot work outside a package either.

The named case in the issue is worse than a crash. `lec_equivalence_check`
wraps its sibling import in `try: ... except ImportError:` and substitutes a
stub returning `None` for the structural port-abort classifier. Loaded by path
it went on emitting the FALSE diagnosis that classifier exists to replace,
with nothing in the output saying the classifier was absent.

Both directions here, and — because a gate over a directory can trivially be a
gate that cannot fail — the isolation properties the sweep depends on are
tested as first-class claims, each with the counter-fixture that would pass
without them.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import program_path_load_check as C  # noqa: E402

_SCRIPT = _PROGRAMS / "program_path_load_check.py"

#: The guard the fix installs, in the shape the programs carry it.
_GUARD = (
    "import os as _os\n"
    "import sys as _sys\n"
    "if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:\n"
    "    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))\n"
)


def _tree(tmp_path: Path, files: dict) -> Path:
    d = tmp_path / "programs"
    d.mkdir(exist_ok=True)
    for name, body in files.items():
        (d / name).write_text(body)
    return d


def _run(programs: Path, json_out: Path, jobs: int = 1):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--programs", str(programs),
         "--jobs", str(jobs), "--json", str(json_out)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True)


def _doc(r, out: Path):
    """The report, or a failure that names why there is no report.

    Reading the JSON first is how the first version of this test was written,
    and the container run that caught it reported a `FileNotFoundError` on
    `r.json` while the real event was the gate exiting 139 — SIGSEGV. A test
    whose message names the wrong thing costs the next reader the hour it cost
    this one.
    """
    if not out.is_file():
        raise AssertionError(
            f"the gate wrote no report. rc={r.returncode}"
            + (f" (killed by signal {-r.returncode})" if r.returncode < 0
               else "")
            + f"\nstdout tail:\n{r.stdout[-1500:]}"
            + f"\nstderr tail:\n{r.stderr[-1500:]}")
    return json.loads(out.read_text())


# ---------------------------------------------------------------------------
# 1. THE TREE ITSELF
# ---------------------------------------------------------------------------
def test_every_shipped_program_loads_by_path(tmp_path):
    """The invariant this issue establishes. Run as a subprocess so the sweep
    cannot be helped, or disturbed, by anything the pytest session imported."""
    out = tmp_path / "r.json"
    r = _run(_PROGRAMS, out, jobs=8)
    doc = _doc(r, out)
    assert doc["unmeasured"] == [], (
        "the gate could not measure "
        f"{[u['program'] for u in doc['unmeasured']][:10]} — that is no "
        "result, not a clean one")
    assert doc["offenders"] == [], (
        f"{len(doc['offenders'])} program(s) cannot resolve a sibling when "
        f"loaded by path: "
        f"{[o['program'] for o in doc['offenders']][:10]}")
    assert doc["measured"] > 1000, doc["measured"]
    assert r.returncode == 0, r.stdout[-2000:]


# ---------------------------------------------------------------------------
# 2. THE CHECK CAN FAIL — and says which sibling
# ---------------------------------------------------------------------------
def test_an_unguarded_sibling_import_is_named(tmp_path):
    d = _tree(tmp_path, {
        "b.py": "VALUE = 1\n",
        "a.py": "from b import VALUE\n",
    })
    out = tmp_path / "r.json"
    r = _run(d, out)
    doc = _doc(r, out)
    assert [o["program"] for o in doc["offenders"]] == ["a.py"]
    assert doc["offenders"][0]["unresolved_sibling"] == "b"
    assert r.returncode == 1


def test_the_same_program_with_the_guard_is_accepted(tmp_path):
    """The pair: only the guard differs between this tree and the one above."""
    d = _tree(tmp_path, {
        "b.py": "VALUE = 1\n",
        "a.py": _GUARD + "from b import VALUE\n",
    })
    out = tmp_path / "r.json"
    r = _run(d, out)
    doc = _doc(r, out)
    assert doc["offenders"] == []
    assert r.returncode == 0


def test_a_sibling_package_counts_as_a_sibling(tmp_path):
    """`programs/_ppa/` is a package and is imported by bare name. Counting
    only `*.py` filed that program under 'not this gate's subject' and shrank
    the population the gate is answerable for."""
    d = _tree(tmp_path, {"a.py": "import pkg\n"})
    (d / "pkg").mkdir()
    (d / "pkg" / "__init__.py").write_text("X = 1\n")
    out = tmp_path / "r.json"
    r = _run(d, out)
    doc = _doc(r, out)
    assert [o["unresolved_sibling"] for o in doc["offenders"]] == ["pkg"]


# ---------------------------------------------------------------------------
# 3. THE ISOLATION THE SWEEP DEPENDS ON
# ---------------------------------------------------------------------------
def test_one_program_may_not_repair_the_next(tmp_path):
    """`a.py` puts the directory on `sys.path` at import time. Without the
    per-file `sys.path` restore, `b.py` — swept after it, in sorted order —
    would resolve `c` and the sweep would report a clean tree. The defect is
    ORDER-DEPENDENT in exactly this way in the real programs directory, which
    is why it stayed latent for so long."""
    d = _tree(tmp_path, {
        "a.py": _GUARD,
        "b.py": "from c import VALUE\n",
        "c.py": "VALUE = 1\n",
    })
    out = tmp_path / "r.json"
    r = _run(d, out)
    doc = _doc(r, out)
    assert [o["program"] for o in doc["offenders"]] == ["b.py"]


def test_the_checkers_own_directory_is_not_on_sys_path(tmp_path):
    """MEASURED while writing this gate: run against a --programs tree other
    than its own, the first version reported PASS on the base tree — 0
    offenders where two independent sweeps had just measured 470. Python puts
    the SCRIPT's directory on `sys.path[0]`, that directory is the REPAIRED
    `programs/`, and every bare import in the tree under test resolved against
    the repaired copy. A gate that answers PASS on a tree known to be broken
    is a second copy of the defect.

    The fixture imports `_path_layout` — a real module of the shipped
    programs directory — with no guard. It must be an offender."""
    d = _tree(tmp_path, {
        "_path_layout.py": "def pnr_dir(p):\n    return p\n",
        "a.py": "from _path_layout import pnr_dir\n",
    })
    out = tmp_path / "r.json"
    r = _run(d, out)
    doc = _doc(r, out)
    assert [o["program"] for o in doc["offenders"]] == ["a.py"]


def test_a_program_that_kills_its_interpreter_is_named_not_fatal(tmp_path):
    """The reason the sweep is one child per file.

    The in-process version was green on the host and exited 139 — SIGSEGV —
    inside the pinned EDA image, partway through the directory, having written
    no report at all. One file can no longer take the sweep with it: it is
    reported by name, the other files still get verdicts, and the run is
    UNMEASURED for that file rather than clean."""
    d = _tree(tmp_path, {
        "a_dies.py": "import os\nos._exit(9)\n",
        "b.py": "VALUE = 1\n",
        "z_offender.py": "from b import VALUE\n",
    })
    out = tmp_path / "r.json"
    r = _run(d, out)
    doc = _doc(r, out)
    assert [u["program"] for u in doc["unmeasured"]] == ["a_dies.py"]
    # the files after the casualty were still measured
    assert [o["program"] for o in doc["offenders"]] == ["z_offender.py"]
    assert doc["measured"] == 3


def test_unmeasured_is_not_reported_as_clean(tmp_path):
    """`I could not look` must never leave as `I looked and it was clean`."""
    d = _tree(tmp_path, {"a_dies.py": "import os\nos._exit(9)\n"})
    out = tmp_path / "r.json"
    r = _run(d, out)
    doc = _doc(r, out)
    assert doc["offenders"] == [] and doc["unmeasured"] != []
    assert r.returncode == 2, r.stdout
    assert "NOT_MEASURED" in r.stdout
    assert "[PASS]" not in r.stdout


def test_a_program_that_prints_at_import_cannot_forge_a_verdict(tmp_path):
    """Several programs in this tree write to stdout at import time. The
    child's verdict is read from a prefixed line, taken LAST, so a program
    that prints — even one that prints something shaped like the verdict —
    cannot answer for itself."""
    d = _tree(tmp_path, {
        "b.py": "VALUE = 1\n",
        "a.py": ("print('__program_path_load_check__ "
                 "{\"verdict\": \"ok\", \"kind\": \"\", "
                 "\"detail\": \"\"}')\n"
                 "from b import VALUE\n"),
    })
    out = tmp_path / "r.json"
    r = _run(d, out)
    doc = _doc(r, out)
    assert [o["program"] for o in doc["offenders"]] == ["a.py"]


def test_jobs_does_not_change_the_answer(tmp_path):
    """Membership, not timing: the parallel sweep must report the same set."""
    d = _tree(tmp_path, {
        "b.py": "VALUE = 1\n",
        "a.py": "from b import VALUE\n",
        "c.py": "from b import VALUE\n",
        "d.py": _GUARD + "from b import VALUE\n",
    })
    o1, o8 = tmp_path / "1.json", tmp_path / "8.json"
    r1 = _run(d, o1, jobs=1)
    r8 = _run(d, o8, jobs=8)
    d1, d8 = _doc(r1, o1), _doc(r8, o8)
    assert ({o["program"] for o in d1["offenders"]}
            == {o["program"] for o in d8["offenders"]} == {"a.py", "c.py"})
    assert r1.returncode == r8.returncode == 1


# ---------------------------------------------------------------------------
# 4. WHAT THIS GATE IS NOT ANSWERABLE FOR
# ---------------------------------------------------------------------------
def test_an_absent_third_party_package_is_not_an_offence(tmp_path):
    """A missing dependency is a fact about the host. Counting it here would
    turn this gate into a report about the machine it ran on, and would let a
    host problem be closed by editing a program."""
    d = _tree(tmp_path, {
        "a.py": "import a_package_that_is_not_installed_anywhere\n"})
    out = tmp_path / "r.json"
    r = _run(d, out)
    doc = _doc(r, out)
    assert doc["offenders"] == []
    assert [o["program"] for o in doc["other"]] == ["a.py"]
    assert r.returncode == 0
    assert "not this gate's subject" in r.stdout


def test_nothing_measured_is_not_a_pass(tmp_path):
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--programs", str(tmp_path / "absent")],
        capture_output=True, text=True)
    assert r.returncode == 2
    assert "NOTHING WAS MEASURED" in r.stdout


# ---------------------------------------------------------------------------
# 5. THE NAMED CASE IN THE ISSUE, LOADED BY PATH AND RUN
# ---------------------------------------------------------------------------
_LEC = _PROGRAMS / "lec_equivalence_check.py"


def _load_by_path(path: Path, code: str) -> subprocess.CompletedProcess:
    """Load `path` by `spec_from_file_location` in a child whose `sys.path`
    does NOT contain the programs directory, then run `code` against it."""
    driver = (
        "import importlib.util, os, sys\n"
        f"P = {str(path)!r}\n"
        f"D = {str(path.parent)!r}\n"
        "sys.path[:] = [p for p in sys.path "
        "if os.path.abspath(p or '.') != D]\n"
        "spec = importlib.util.spec_from_file_location('m', P)\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "sys.modules['m'] = m\n"
        "spec.loader.exec_module(m)\n"
        + code
    )
    return subprocess.run([sys.executable, "-c", driver],
                          capture_output=True, text=True, cwd=str(path.parent.parent))


def test_lec_equivalence_check_keeps_the_real_classifier_by_path():
    """On the base tree this printed `False` and the classifier was a stub
    that returns None for every input — the gate then reported the FALSE
    diagnosis it exists to replace, and said nothing about the substitution."""
    r = _load_by_path(_LEC, "print('HAS_GNS', m._HAS_GNS)\n"
                            "print('FROM', m._classify_port_abort.__module__)\n")
    assert r.returncode == 0, r.stderr[-2000:]
    assert "HAS_GNS True" in r.stdout, r.stdout + r.stderr
    assert "FROM lec_gate_netlist_select" in r.stdout, r.stdout


def test_lec_equivalence_check_entry_point_runs_when_loaded_by_path(tmp_path):
    """Importable is not the claim; the entry point has to run. An empty
    project is a state the gate has a verdict for, so a real exit code comes
    back rather than an exception."""
    r = _load_by_path(_LEC,
                      f"rc = m.main([{str(tmp_path)!r}])\n"
                      "print('RC', rc)\n")
    assert r.returncode == 0, r.stderr[-2000:]
    assert "RC " in r.stdout, r.stdout + r.stderr
    rc = int(r.stdout.split("RC ")[1].split()[0])
    assert rc in (0, 1, 2), r.stdout


# ---------------------------------------------------------------------------
# 6. THE IN-PROCESS ENTRY POINTS, DIRECTLY
# ---------------------------------------------------------------------------
def test_sibling_names_includes_modules_and_packages(tmp_path):
    d = _tree(tmp_path, {"a.py": "", "b.py": ""})
    (d / "pkg").mkdir()
    (d / "pkg" / "__init__.py").write_text("")
    (d / "notapkg").mkdir()
    assert C.sibling_names(d) == {"a", "b", "pkg"}


def test_load_isolated_restores_sys_path_and_sys_modules(tmp_path):
    d = _tree(tmp_path, {"a.py": _GUARD + "MARK = 1\n"})
    before_path, before_mods = list(sys.path), set(sys.modules)
    verdict, _kind, _detail = C.load_isolated(d / "a.py", d)
    assert verdict == "ok"
    assert sys.path == before_path
    assert set(sys.modules) == before_mods


# ---------------------------------------------------------------------------
# 4. THE CHECKER'S OWN IMPORTS MAY NOT SEED THE CHILD  (vibe-ic#2154 / #2175)
# ---------------------------------------------------------------------------
#
# `test_one_program_may_not_repair_the_next` pins the property BETWEEN two
# programs of the subject; these two pin it between the CHECKER and the subject,
# which is a different door into the same room and was open.
#
# MEASURED while making the `--json` write atomic for #2154. The obvious repair
# — `from _atomic_artefact import write_json` at module level, the shape every
# other converted program in this tree uses — was applied and driven over a
# two-program subject holding `_atomic_artefact.py` and one bare import of it:
#
#     module-level import in program_path_load_check  ->  [PASS] rc 0, 0 offenders
#     deferred to the write site                      ->  [FAIL] rc 1, 1 offender
#
# `--one` runs in the same interpreter that has already executed the checker's
# module body, so anything imported up there is in `sys.modules` before
# `load_isolated` snapshots it; the `finally` deletes only what the load ADDED,
# so a pre-seeded sibling survives and every bare import of it in the tree under
# test resolves for free. Removing the directory from `sys.path` cannot help —
# the module object is already there.
#
# Nothing in the file above would have caught it: every fixture sibling here is
# named `a`, `b`, `c` or `_path_layout`, and the checker imports none of those.
def test_a_module_the_checker_imports_is_not_pre_seeded_for_the_subject(tmp_path):
    """A subject sibling sharing a name with one of the checker's own imports
    must still be an offence. `_atomic_artefact` is the live instance."""
    d = _tree(tmp_path, {
        "_atomic_artefact.py": "def write_json(p, o):\n    return p\n",
        "zz_bare_user.py": "from _atomic_artefact import write_json\n",
    })
    out = tmp_path / "r.json"
    r = _run(d, out)
    doc = _doc(r, out)
    assert [o["program"] for o in doc["offenders"]] == ["zz_bare_user.py"], doc
    assert doc["offenders"][0]["unresolved_sibling"] == "_atomic_artefact", doc
    assert r.returncode == 1, r.stdout[-2000:]


def test_the_checker_imports_nothing_from_its_own_directory_at_module_level():
    """THE GUARD FOR THE TEST ABOVE, because that test only covers the ONE name
    that happens to be imported today.

    A future sibling import added at module scope re-opens the hole for its own
    name, and the behavioural test above cannot see it. So the rule is stated
    over the module's own syntax: `program_path_load_check` imports only the
    standard library at module scope, and any sibling helper it needs is
    imported inside the function that uses it — after `--one` has returned.
    """
    import ast
    import sys as _sys

    tree = ast.parse(_SCRIPT.read_text())
    siblings = C.sibling_names(_PROGRAMS)
    stdlib = getattr(_sys, "stdlib_module_names", frozenset())
    module_level = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            module_level.append(node.module.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level:
            module_level.append("." * node.level)
    offending = sorted(n for n in module_level
                       if n in siblings or n.startswith("."))
    assert not offending, (
        f"{_SCRIPT.name} imports {offending} at MODULE level. Every one of "
        "those names is seeded into `sys.modules` before `--one` sweeps a "
        "file, so a subject program that bare-imports a sibling of that name "
        "resolves it for free and the gate answers PASS over the exact offence "
        "it exists to name. Import it inside the function that uses it.")
    # And the assertion above is not vacuous only because the sibling set is
    # real: `stdlib` is checked so a future rename of `sibling_names` that
    # returned nothing would be caught here rather than passing silently.
    assert siblings and "argparse" not in siblings, sorted(siblings)[:5]
    assert not stdlib or "json" in stdlib


# ---------------------------------------------------------------------------
# 5. THE SWEEP READS ITS SUBJECT AND DOES NOT WRITE INTO IT  (vibe-ic#2175)
# ---------------------------------------------------------------------------
def test_the_sweep_leaves_no_bytecode_in_the_tree_it_measures(tmp_path,
                                                              monkeypatch):
    """A gate that only needs to READ a tree must not write into it.

    Each child IMPORTS a program of the subject, so CPython writes
    `<subject>/__pycache__/*.pyc` for every one of them unless bytecode writing
    is off IN THAT PROCESS. `sys.dont_write_bytecode` does not cross a
    subprocess boundary and neither does the parent's `-B`, so the flag has to
    be on the child's own argv.

    MEASURED before the flag was added, on this fixture: `__pycache__` present
    in the subject after the sweep. The residue is invisible to `git status`
    (`.gitignore`) and visible to `attestation_preflight_check`, which is the
    13-of-39 differential that gate was written from.

    `PYTHONDONTWRITEBYTECODE` is REMOVED from this test's environment on
    purpose. `repo_hygiene_gates.sh:52` exports it, so inheriting it would make
    this test pass on the dispatcher's behalf and say nothing about the gate —
    and the gate is also run directly, by the file above and by any operator.
    """
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    d = _tree(tmp_path, {
        "leaf_mod.py": "VALUE = 1\n",
        "user_mod.py": _GUARD + "from leaf_mod import VALUE\n",
    })
    out = tmp_path / "r.json"
    r = _run(d, out)
    doc = _doc(r, out)
    assert doc["offenders"] == [] and doc["measured"] == 2, doc
    residue = sorted(str(p.relative_to(tmp_path))
                     for p in tmp_path.rglob("__pycache__"))
    assert not residue, (
        "the sweep wrote bytecode into the tree it was asked to measure: "
        f"{residue}. Pass `-B` on the child's argv — the parent's flag and "
        "`sys.dont_write_bytecode` do not cross a subprocess boundary.")
    assert r.returncode == 0, r.stdout[-2000:]
