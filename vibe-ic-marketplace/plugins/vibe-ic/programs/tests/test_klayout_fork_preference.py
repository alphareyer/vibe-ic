#!/usr/bin/env python3
"""The flow must run OUR klayout, and both halves of that are load bearing.

vibeic-eda#17. The fork patches the LEF/DEF importer to honour tech-LEF
MANUFACTURINGGRID. Measured on the fork's own fixture — same DEF, same tech
LEF, 5 nm grid:

    base pymod                          OFFGRID_VERTICES_TOTAL = 8
    fork pymod                          OFFGRID_VERTICES_TOTAL = 0
    fork pymod + base LD_LIBRARY_PATH   OFFGRID_VERTICES_TOTAL = 8   <-- the trap

The last line is why these tests exist. The pymod extension modules link
libklayout_db.so by SONAME, so pointing sys.path at our build while the base
directory is on the library search path reproduces the base's behaviour exactly.
A fix that moved only sys.path would look right and change nothing.

These drive the real fragments — the shell one through a real shell, the Python
one through a real interpreter — rather than asserting that the source contains
a string.
"""
from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as P                            # noqa: E402


def _run_preamble(tmp_path, dirs):
    """Execute the real shell fragment against a fabricated /foss/tools."""
    root = tmp_path / "foss" / "tools"
    for d in dirs:
        (root / d).mkdir(parents=True)
        exe = root / d / "klayout"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    frag = P.KLAYOUT_PREFER_FORK_SH.replace("/foss/tools/", f"{root}/")
    out = subprocess.run(
        ["bash", "-c", frag + 'echo "$PATH|$LD_LIBRARY_PATH|$KLAYOUT_PYMOD"'],
        capture_output=True, text=True, env={**os.environ, "LD_LIBRARY_PATH": ""})
    path, ldp, pymod = out.stdout.strip().split("|")
    return path.split(":")[0], ldp.split(":")[0], pymod


def test_the_fork_wins_when_the_image_has_one(tmp_path):
    first_path, first_lib, pymod = _run_preamble(
        tmp_path, ["klayout", "klayout-vibeic"])
    assert first_path.endswith("klayout-vibeic")
    assert first_lib.endswith("klayout-vibeic"), \
        "PATH alone is not enough — the libraries decide the geometry"
    assert pymod.endswith("klayout-vibeic/pymod")


def test_the_base_is_used_when_there_is_no_fork(tmp_path):
    """A stock iic-osic-tools image must still work."""
    first_path, first_lib, pymod = _run_preamble(tmp_path, ["klayout"])
    assert first_path.endswith("/klayout")
    assert first_lib.endswith("/klayout")
    assert pymod.endswith("/klayout/pymod")


def test_the_python_half_follows_the_shell_half(tmp_path):
    """One resolution, not two: a binary and its libraries from one build.

    The Python fragment reads what the shell already chose. Two independent
    resolutions is how you end up running our binary against the base's
    libraries — which measures as the base.
    """
    chosen = tmp_path / "chosen-pymod"
    chosen.mkdir()
    out = subprocess.run(
        [sys.executable, "-c", P.KLAYOUT_PYMOD_PY + "print(sys.path[0])"],
        capture_output=True, text=True,
        env={**os.environ, "KLAYOUT_PYMOD": str(chosen)})
    assert out.stdout.strip() == str(chosen), out.stderr[-300:]


def test_the_python_half_still_resolves_with_no_shell_hint():
    """Fall back rather than crash when the fragment is used on its own."""
    out = subprocess.run(
        [sys.executable, "-c", P.KLAYOUT_PYMOD_PY + "print('ok')"],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "KLAYOUT_PYMOD"})
    assert out.returncode == 0 and "ok" in out.stdout


def _docstring_ids(tree):
    """The `ast.Constant` nodes that are PROSE, not call-site values.

    A DOCSTRING IS NOT A CALL SITE, and reading one as a hard-code is how this
    guard accused an honest landing. MEASURED (czmainred9, v1.19.60): `8000bb196`
    documented WHY `_local_exec_mode` exists by quoting the measurement that
    produced it —

        `yosys`, `openroad` and `klayout` were all on PATH in that same process
        (/foss/tools/bin/yosys, /foss/tools/bin/openroad,
        /foss/tools/klayout/klayout)

    — inside the function's docstring. That is a report of where the tools were
    found on one host, it invokes nothing, and this scan named it as a site that
    hard-codes the base tree. The string it flagged does not even reach a
    process; a fix "for" it could only have been to delete the evidence.

    A string in EXPRESSION POSITION executed for no effect is the same class
    `gate_is_wired_check.executable_text` drops for the same reason, and that
    docstring rule is the one respelled here: this scan needs the NODE (to keep
    `node.lineno` for the message) where `executable_text` returns text, so it
    cannot call it — but it must not disagree with it either. Comments never
    reach `ast` at all, so only docstrings need saying.

    THIS IS NOT A CARVE-OUT FOR THE OFFENDING LINE. Any string that a call site
    passes still counts, including one built or bound in the same function;
    `test_a_hard_code_in_a_call_site_is_still_caught` plants one and proves it.
    """
    out = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                       ast.ClassDef)):
            continue
        first = body[0] if body else None
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            out.add(id(first.value))
    return out


def test_no_call_site_hard_codes_the_base_klayout():
    """WIRING, which is where a fix like this leaks.

    Stated limit: this reads the call sites rather than executing them — the
    real ones need a container and a GDS. It can only catch a site that names
    the base tree directly, which is exactly how both of the sites this issue
    found were written.

    A string that names the base tree AND the fork tree is a resolver, and the
    base path in it is the fallback that keeps a stock image working. A string
    that names only the base is a hard-code. That distinction is the property,
    not a carve-out for the two constants — a resolver written tomorrow passes,
    and a hard-code hidden inside one does not.
    """
    src = pathlib.Path(P.__file__).read_text()
    tree = ast.parse(src)
    docstrings = _docstring_ids(tree)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue                 # PROSE. See `_docstring_ids`.
            v = node.value
            names_base = ("/foss/tools/klayout/" in v
                          or v.endswith("/foss/tools/klayout"))
            if names_base and "klayout-vibeic" not in v:
                offenders.append((node.lineno, v[:70]))
    assert not offenders, (
        "these name the BASE klayout tree directly, so the fork never runs "
        "there: " + "; ".join(f"line {l}: {v}" for l, v in offenders))


def test_a_hard_code_in_a_call_site_is_still_caught():
    """MUTATION. The docstring exemption must not be an exemption for anything
    that runs.

    Both directions on one synthetic module: the SAME path string is invisible
    in a docstring and an offender everywhere a call site can put it — bound to
    a name, passed as an argument, built into an f-string, or sitting in a list.
    Drop `_docstring_ids` and the first assertion fails; widen it to any string
    constant and every one of the four below goes quiet.
    """
    mod = ast.parse(
        '"""prose: measured at /foss/tools/klayout/klayout on one host."""\n'
        'BOUND = "/foss/tools/klayout/klayout"\n'
        'def go(run):\n'
        '    """also prose about /foss/tools/klayout/klayout."""\n'
        '    run("/foss/tools/klayout/strmcmp")\n'
        '    run(f"{PRE}/foss/tools/klayout/x")\n'
        '    return ["/foss/tools/klayout"]\n')
    docstrings = _docstring_ids(mod)
    hits = [n.value for n in ast.walk(mod)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings
            and ("/foss/tools/klayout/" in n.value
                 or n.value.endswith("/foss/tools/klayout"))]
    assert sorted(hits) == [
        "/foss/tools/klayout",
        "/foss/tools/klayout/klayout",
        "/foss/tools/klayout/strmcmp",
        "/foss/tools/klayout/x",
    ], hits


def test_the_docstring_rule_agrees_with_the_trees_one_answer():
    """`_docstring_ids` must not become a SECOND, divergent docstring rule.

    vibe-ic#2169's finding was two implementations of one predicate drifting
    apart. This asserts the two agree on the real file: every string this scan
    skips is a string `gate_is_wired_check.executable_text` also removes.
    """
    sys.path.insert(0, str(pathlib.Path(P.__file__).parent))
    import gate_is_wired_check as wiring        # noqa: E402

    path = pathlib.Path(P.__file__)
    src = path.read_text()
    kept = wiring.executable_text(path, src)
    tree = ast.parse(src)
    lines = src.splitlines()
    kept_lines = kept.splitlines()
    docstrings = _docstring_ids(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue
        if id(node.value) not in docstrings:
            continue
        lo, hi = node.lineno, (node.end_lineno or node.lineno)
        assert all(not kept_lines[i].strip()
                   for i in range(lo - 1, min(hi, len(lines)))), (
            f"line {lo}-{hi} is a docstring to this scan and executable to "
            f"gate_is_wired_check.executable_text — the two rules have drifted")
