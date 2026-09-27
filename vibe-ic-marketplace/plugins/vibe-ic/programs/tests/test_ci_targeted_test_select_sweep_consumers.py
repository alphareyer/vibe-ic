#!/usr/bin/env python3
"""Rule 8b: a test that hands `programs/` to a program that globs it is
selected when any `programs/*.py` changes.

WHY THIS FILE EXISTS
====================
`test_absence_verdict_names_its_search_space.py::test_the_shipped_tree_is_clean`
went red after a landing THREE times. The third time, three landings each added
an absence refusal to a `programs/*.py` (T99 9bd7eb1bb, T101 d255f9aad, T104
29d5b291b), and on each diff the landing selector (import-edge mode) picked
665-685 files and not that test. The test reads every program, but through
`AV.scan(PROGRAMS)`: the glob is in the PROGRAM, the root is chosen by the
TEST, and rule 8 only reads globs written in the test tree.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parent.parent.parent
_PREFIX = "vibe-ic-marketplace/plugins/vibe-ic"
_ABSENCE = "programs/tests/test_absence_verdict_names_its_search_space.py"


def _selector():
    path = _PLUGIN / "programs" / "ci_targeted_test_select.py"
    spec = importlib.util.spec_from_file_location("_sel_sweep8b", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_sel_sweep8b"] = mod
    spec.loader.exec_module(mod)
    return mod


SEL = _selector()


def _fake_plugin(root: Path, tests: dict[str, str]) -> Path:
    (root / "programs" / "tests").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = programs/tests\n")
    (root / "programs" / "sweeper.py").write_text(
        "from pathlib import Path\n\n\n"
        "def scan(root):\n"
        "    return [p.name for p in sorted(Path(root).rglob('*.py'))]\n")
    (root / "programs" / "flat_sweeper.py").write_text(
        "from pathlib import Path\n\n\n"
        "def scan(root):\n"
        "    return [p.name for p in sorted(Path(root).glob('*.py'))]\n")
    (root / "programs" / "new_program.py").write_text("x = 1\n")
    for name, body in tests.items():
        (root / "programs" / "tests" / name).write_text(body)
    return root


_LOAD = (
    "import importlib.util, sys\n"
    "from pathlib import Path\n"
    "PROGRAMS = Path(__file__).resolve().parent.parent\n"
    "_spec = importlib.util.spec_from_file_location("
    "'{stem}', PROGRAMS / '{stem}.py')\n"
    "S = importlib.util.module_from_spec(_spec)\n"
    "_spec.loader.exec_module(S)\n")


def _changed(root: Path, *rels: str) -> list[str]:
    return SEL.select_tests(list(rels), root, "", mode=SEL.MODE_IMPORT_EDGE)


def test_the_absence_test_comes_with_any_changed_program():
    """The regression itself, on the real tree: a change to a program the test
    never names must select it in the mode the landing runs
    (`tools/ci/trusted_test_selection.py` passes import-edge)."""
    assert (_PLUGIN / _ABSENCE).is_file()
    unrelated = sorted(p for p in (_PLUGIN / "programs").glob("*.py")
                       if "absence" not in p.name)[0]
    changed = [f"{_PREFIX}/programs/{unrelated.name}"]
    sel = SEL.select_tests(changed, _PLUGIN, _PREFIX, mode=SEL.MODE_IMPORT_EDGE)
    assert _ABSENCE in sel, (unrelated.name, len(sel))


def test_a_test_handing_the_directory_to_a_sweeper_is_selected(tmp_path):
    root = _fake_plugin(tmp_path, {
        "test_sweeps.py": _LOAD.format(stem="sweeper")
        + "\n\ndef test_it():\n    assert S.scan(PROGRAMS)\n",
        "test_sweeps_by_argv.py": (
            "import subprocess, sys\n"
            "from pathlib import Path\n"
            "PROGRAMS = Path(__file__).resolve().parent.parent\n\n\n"
            "def test_it():\n"
            "    subprocess.run([sys.executable, str(PROGRAMS / 'sweeper.py'),\n"
            "                    str(PROGRAMS)], check=True)\n"),
    })
    sel = _changed(root, "programs/new_program.py")
    assert "programs/tests/test_sweeps.py" in sel
    assert "programs/tests/test_sweeps_by_argv.py" in sel


def test_a_dependent_that_points_the_sweep_elsewhere_is_not_selected(tmp_path):
    """The hand-over half is what makes the edge specific: importing a globbing
    program for something else, or aiming it at another directory, reads
    nothing under `programs/`."""
    root = _fake_plugin(tmp_path, {
        "test_imports_only.py": _LOAD.format(stem="sweeper")
        + "sys.path.insert(0, str(PROGRAMS))\n"
        + "\n\ndef test_it():\n    assert S.scan\n",
        "test_sweeps_tmp.py": _LOAD.format(stem="sweeper")
        + "\n\ndef test_it(tmp_path):\n    assert S.scan(tmp_path) == []\n",
        "test_flat_on_tests_dir.py": _LOAD.format(stem="flat_sweeper")
        + "\n\ndef test_it():\n    assert S.scan(Path(__file__).parent)\n",
    })
    sel = _changed(root, "programs/new_program.py")
    for name in ("test_imports_only.py", "test_sweeps_tmp.py",
                 "test_flat_on_tests_dir.py"):
        assert f"programs/tests/{name}" not in sel, name


def test_a_non_recursive_glob_needs_the_files_own_directory(tmp_path):
    """`.glob('*.py')` on the plugin root does not read `programs/x.py`;
    `.rglob('*.py')` on it does."""
    body = ("\n\ndef test_it():\n"
            "    assert S.scan(Path(__file__).resolve().parents[2]) is not None\n")
    root = _fake_plugin(tmp_path, {
        "test_flat_on_root.py": _LOAD.format(stem="flat_sweeper") + body,
        "test_recursive_on_root.py": _LOAD.format(stem="sweeper") + body,
    })
    sel = _changed(root, "programs/new_program.py")
    assert "programs/tests/test_recursive_on_root.py" in sel
    assert "programs/tests/test_flat_on_root.py" not in sel


def test_a_pattern_that_does_not_match_the_change_selects_nothing(tmp_path):
    root = _fake_plugin(tmp_path, {
        "test_sweeps.py": _LOAD.format(stem="sweeper")
        + "\n\ndef test_it():\n    assert S.scan(PROGRAMS)\n",
    })
    (root / "programs" / "notes.json").write_text("{}\n")
    assert "programs/tests/test_sweeps.py" not in _changed(root, "programs/notes.json")
    assert "programs/tests/test_sweeps.py" in _changed(root, "programs/new_program.py")


def test_ownership_mode_counts_only_owned_dependents(tmp_path):
    """The cheap lane keeps its own notion of "depends on": a sweep test named
    after its program comes, one that only loads the program does not."""
    body = "\n\ndef test_it():\n    assert S.scan(PROGRAMS)\n"
    root = _fake_plugin(tmp_path, {
        "test_sweeper.py": _LOAD.format(stem="sweeper") + body,
        "test_loads_sweeper.py": _LOAD.format(stem="sweeper") + body,
    })
    sel = SEL.select_tests(["programs/new_program.py"], root, "",
                           mode=SEL.MODE_OWNERSHIP)
    assert "programs/tests/test_sweeper.py" in sel
    assert "programs/tests/test_loads_sweeper.py" not in sel
