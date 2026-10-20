"""#3319: the normal installer must expose its serving bundle, read-only.

These drive the existing Docker-argv harness; the separate native receipt
executes the unchanged pad producer under these generated mounts.
"""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "restart_mount_fixture", Path(__file__).with_name("test_restart_eda_mount_preservation.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
Harness, _state = _fixture.Harness, _fixture._state
COPIES = (PLUGIN.parents[2] / "tools/vibeic-eda/restart-eda.sh",
          PLUGIN / "tools/vibeic-eda/restart-eda.sh")
RUNTIME_BIND = f"{PLUGIN}:{PLUGIN}:ro"


@pytest.mark.parametrize("script", COPIES, ids=("repository", "packaged"))
def test_fresh_install_exposes_the_whole_serving_bundle(tmp_path, script):
    designs = tmp_path / "designs"
    designs.mkdir()
    h = Harness(tmp_path, _state(exists=False))
    result = h.run(script, DESIGNS_DIR=designs)
    assert result.returncode == 0, result.stderr
    assert h.mount_args() == [RUNTIME_BIND, f"{designs}:{designs}",
                              f"{designs}:/foss/designs"]
    assert h.run_argv()[-3:] == ["--skip", "sleep", "infinity"]


@pytest.mark.parametrize("script", COPIES, ids=("repository", "packaged"))
def test_restart_keeps_design_and_pdk_mounts_with_serving_bundle(tmp_path, script):
    designs, pdk = tmp_path / "designs", tmp_path / "pdk"
    designs.mkdir()
    pdk.mkdir()
    h = Harness(tmp_path, _state(
        binds=[f"{designs}:{designs}:rw", f"{designs}:/foss/designs:rw"],
        mounts=[{"Type": "bind", "Source": str(pdk), "Target": "/pdk",
                 "ReadOnly": True}]))
    result = h.run(script)
    assert result.returncode == 0, result.stderr
    assert h.mount_args() == [RUNTIME_BIND, f"{designs}:{designs}:rw",
                              f"{designs}:/foss/designs:rw", f"{pdk}:/pdk:ro"]


@pytest.mark.parametrize("script", COPIES, ids=("repository", "packaged"))
def test_old_mapping_at_serving_path_is_replaced_readonly(tmp_path, script):
    old = tmp_path / "old-bundle"
    old.mkdir()
    h = Harness(tmp_path, _state(binds=[f"{old}:{PLUGIN}:rw"]))
    result = h.run(script)
    assert result.returncode == 0, result.stderr
    assert h.mount_args() == [RUNTIME_BIND]


@pytest.mark.parametrize("script", COPIES, ids=("repository", "packaged"))
def test_readback_must_confirm_the_serving_bundle_mount(tmp_path, script):
    h = Harness(tmp_path, _state())
    fake = h.bin / "docker"
    fake.write_text(fake.read_text().replace(
        "return res\n", f"return [m for m in res if m[0] != {str(PLUGIN)!r}]\n"))
    result = h.run(script)
    assert result.returncode == 3, (result.stdout, result.stderr)
    assert str(PLUGIN) in result.stderr
    assert "readback failed" in result.stderr
    assert any(c[0] == "rename" and c[-1] == "vibeic-eda" for c in h.calls())


def _installed_upgrade(tmp_path, script, form, *, other=None, readonly=True):
    """Install actual public bytes; only the old cache slot is absent."""
    client = ".codex" if form == "binds" else ".claude"
    family = tmp_path / client / "plugins/cache/vibe-ic-marketplace/vibe-ic"
    version = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())["version"]
    current, old = family / version, family / "1.30.41"
    installed = current / "tools/vibeic-eda/restart-eda.sh"
    installed.parent.mkdir(parents=True)
    shutil.copyfile(script, installed)
    for rel in ("programs/_docker_memory.py", ".claude-plugin/plugin.json"):
        target = current / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PLUGIN / rel, target)
    designs, pdk = tmp_path / "designs", tmp_path / "pdk"
    custom = tmp_path / "custom/vibe-ic/1.30.41"
    for path in (designs, pdk, custom):
        path.mkdir(parents=True)
    source, target = other(family, old) if other else (old, old)
    declarations = [(str(source), str(target), "ro" if readonly else "rw"),
                    (str(designs), "/foss/designs", "rw,rshared"),
                    (str(pdk), "/pdk", "ro,rslave"),
                    (str(custom), "/custom-plugin", "ro")]
    if form == "binds":
        state = _state(binds=[":".join(d) for d in declarations])
    else:
        state = _state(mounts=[
            {"Type": "bind", "Source": src, "Target": dst,
             "ReadOnly": "ro" in opts.split(","),
             "BindOptions": {"Propagation": opts.split(",")[1]
                             if "," in opts else ""}}
            for src, dst, opts in declarations])
    h = Harness(tmp_path, state)
    return h, installed, current, old, declarations


def _run_upgrade(h, installed):
    result = h.run(installed)
    (h.dir / "restart.stdout").write_text(result.stdout)
    (h.dir / "restart.stderr").write_text(result.stderr)
    (h.dir / "restart.rc").write_text(str(result.returncode) + "\n")
    return result


@pytest.mark.parametrize("script", COPIES, ids=("repository", "packaged"))
@pytest.mark.parametrize("form", ("binds", "mounts"))
def test_upgrade_replaces_missing_managed_bundle(tmp_path, script, form):
    h, installed, current, old, declarations = _installed_upgrade(tmp_path, script, form)
    assert not old.exists() and current.is_dir() and old != current
    result = _run_upgrade(h, installed)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr,
                                    h.calls())
    # Structured ReadOnly=false uses Docker's default RW, with propagation
    # retained; legacy Binds keep their explicit rw option verbatim.
    expected = [":".join(d) for d in declarations[1:]]
    if form == "mounts":
        expected[0] = expected[0].replace(":rw,rshared", ":rshared")
    assert h.mount_args() == [f"{current}:{current}:ro"] + expected
    argv = h.run_argv()
    assert argv[argv.index("-u") + 1] == "1000"
    assert argv[argv.index("-w") + 1] == "/foss/designs"
    assert argv[-3:] == ["--skip", "sleep", "infinity"]
    assert argv[argv.index("--memory") + 1] == "8g"
    assert argv[argv.index("--memory-swap") + 1] == "8g"
    assert "OK: all 4 declared mount(s) present after readback" in result.stdout


@pytest.mark.parametrize("script", COPIES, ids=("repository", "packaged"))
@pytest.mark.parametrize("form", ("binds", "mounts"))
@pytest.mark.parametrize("case", ("design", "pdk", "lookalike", "namespace", "alias", "rw"))
def test_upgrade_preserves_missing_unmanaged_mount_refusal(tmp_path, script, form, case):
    def other(family, old):
        if case == "lookalike":
            path = tmp_path / "custom/vibe-ic/1.30.40"
            return path, path
        if case == "alias":
            return old, Path("/custom-alias")
        if case == "namespace":
            path = family.parent.parent / "other-marketplace/vibe-ic/1.30.41"
            return path, path
        return old, old

    h, installed, current, old, declarations = _installed_upgrade(
        tmp_path, script, form, other=other, readonly=case != "rw")
    if case in ("design", "pdk"):
        # Make the obsolete bind present so the missing user mount is the
        # exact preflight refusal, independently of the migration rule.
        old.mkdir()
        (tmp_path / ("designs" if case == "design" else "pdk")).rmdir()
    result = _run_upgrade(h, installed)
    assert result.returncode == 4, (result.returncode, result.stdout, result.stderr)
    assert "does not exist" in result.stderr
    missing = (tmp_path / ("designs" if case == "design" else "pdk")
               if case in ("design", "pdk") else declarations[0][0])
    assert f"mount source '{missing}'" in result.stderr
    assert h.run_argv() is None
    assert not any(c[0] in {"rm", "rename", "stop", "start", "run", "kill"}
                   for c in h.calls()), h.calls()
