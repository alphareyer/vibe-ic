"""Bidirectional controls for the source-owned production worker venue.

The fixtures below are neutral source shapes. The first test reads the shipped
production modules, while each following arm removes one part of the actual
registration/component route. Imports, copied modules and self-reference must
not clear an orphan finding.
"""
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flow_gate_enforcement_audit as audit  # noqa: E402


PROGRAMS = Path(__file__).resolve().parents[1]


def _fixture(root: Path) -> Path:
    target = root / "programs"
    target.mkdir()
    names = (
        "execution_policy.py", "execution_production.py",
        "execution_step_protocol.py", "execution_modes.py",
        "execution_adapters_frontend.py", "execution_frontend_worker.py",
        "execution_adapters_backend.py", "execution_adapters_release.py",
        "execution_adapters_analog.py",
    )
    for name in names:
        shutil.copy2(PROGRAMS / name, target / name)
    return target


def _replace_default_factory(programs: Path, old: str, new: str) -> None:
    policy = programs / "execution_policy.py"
    text = policy.read_text()
    updated, count = re.subn(
        rf"(?m)^DEFAULT_FACTORIES = \('{re.escape(old)}',",
        f"DEFAULT_FACTORIES = ('{new}',",
        text,
    )
    assert count == 1, "fixture policy default registration moved"
    policy.write_text(updated)


def test_registered_default_factory_reaches_actual_controller_component():
    report = audit.production_worker_reachability(PROGRAMS, "execution_frontend_worker")
    assert report["dispatch_proof"] is True, report
    assert report["reached"] is True, report
    route = next(r for r in report["routes"] if r["worker"] == "execution_frontend_worker")
    assert route["registered_factory"] == "execution_adapters_frontend", route
    assert route["component"] == "Adapter.Component", route
    assert route["bound_path"].endswith("/execution_frontend_worker.py"), route


def test_import_only_registered_module_does_not_reach_worker(tmp_path):
    programs = _fixture(tmp_path)
    (programs / "import_only.py").write_text(
        "from execution_frontend_worker import produce\n"
        "STEP_IDS = ('D1',)\n"
        "def prepare(request):\n"
        "    return None\n"
    )
    _replace_default_factory(programs, "execution_adapters_frontend", "import_only")
    report = audit.production_worker_reachability(programs, "execution_frontend_worker")
    assert report["reached"] is False, report


def test_self_reference_is_not_a_component_route(tmp_path):
    programs = _fixture(tmp_path)
    report = audit.production_worker_reachability(programs, "flow_gate_enforcement_audit")
    assert report["reached"] is False, report


def test_copied_unregistered_factory_does_not_clear_its_worker(tmp_path):
    programs = _fixture(tmp_path)
    copied = programs / "copied_factory.py"
    copied.write_text(
        "from pathlib import Path\n"
        "import execution_modes as em\n"
        "STEP_IDS = ('D1',)\n"
        "def prepare(request):\n"
        "    component = em.Component('copied', (str(Path(__file__).resolve()),))\n"
        "    return component\n"
    )
    report = audit.production_worker_reachability(programs, "copied_factory")
    assert report["reached"] is False, report


def test_repointed_component_does_not_reach_declared_worker(tmp_path):
    programs = _fixture(tmp_path)
    worker = programs / "execution_frontend_worker.py"
    text = worker.read_text()
    old = "str(Path(__file__).resolve()),\n            '--phase'"
    assert old in text
    worker.write_text(text.replace(
        old,
        "str(Path(__file__).resolve().parent / 'unwired_worker.py'),\n            '--phase'",
        1,
    ))
    report = audit.production_worker_reachability(programs, "execution_frontend_worker")
    assert report["reached"] is False, report
    assert report["unresolved"] == [], report


def test_registered_component_returned_without_prepared_step_does_not_reach_worker(tmp_path):
    programs = _fixture(tmp_path)
    (programs / "unused_component.py").write_text(
        "from pathlib import Path\n"
        "import execution_modes as em\n"
        "STEP_IDS = ('D1',)\n"
        "def prepare(request):\n"
        "    component = em.Component('unused', (str(Path(__file__).resolve().parent / 'execution_frontend_worker.py'),))\n"
        "    return None\n"
    )
    _replace_default_factory(programs, "execution_adapters_frontend", "unused_component")
    report = audit.production_worker_reachability(programs, "execution_frontend_worker")
    assert report["reached"] is False, report
    assert "execution_frontend_worker" not in report["reachable_workers"], report


def test_disconnected_controller_launch_does_not_prove_component_argv(tmp_path):
    programs = _fixture(tmp_path)
    modes = programs / "execution_modes.py"
    text = modes.read_text()
    old = "for v in component.argv"
    assert old in text
    modes.write_text(text.replace(old, "for v in unrelated.argv", 1))
    report = audit.production_worker_reachability(programs, "execution_frontend_worker")
    assert report["dispatch_proof"] is False, report
    assert report["reached"] is False, report
    assert any("component.argv" in item for item in report["unresolved"]), report
