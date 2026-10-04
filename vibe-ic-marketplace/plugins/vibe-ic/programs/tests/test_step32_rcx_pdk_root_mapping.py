"""Focused Step32 RCX PDK-root binding controls."""
import json
import subprocess
import sys
import types
from pathlib import Path

import _plugin_tree  # noqa: F401
import librelane_contract as ll


def test_generated_resolver_rebases_pdk_paths_in_its_container_script(
        tmp_path, monkeypatch):
    """Execute the resolver payload itself, including its private imports."""
    project = tmp_path / "project"
    project.mkdir()
    pdk_root = tmp_path / "pdk-root"
    (pdk_root / "gf180mcuD").mkdir(parents=True)
    monkeypatch.setenv("PDK_ROOT", "/foss/pdks")
    monkeypatch.setattr(ll, "image_capability", lambda *_a, **_k: {})
    monkeypatch.setattr(ll, "_check_synthesised_read", lambda *_a, **_k: None)
    monkeypatch.setattr(ll, "_apply_runner_floorplan", lambda *_a, **_k: None)
    monkeypatch.setattr(ll, "_apply_layout_top", lambda *_a, **_k: None)

    def emit(_project, _pdk, output):
        ll.write_json(output, {})
        ll.write_json(output.with_suffix(".provenance.json"), {})
        return {}

    monkeypatch.setattr(ll, "emit_config", emit)
    design = project / "phase3/generated/design.json"
    requested = project / "phase3/generated/steps.json"

    class Var:
        def __init__(self, name):
            self.name = name

    class Target:
        def get_all_config_variables(self):
            return [Var("PDK_ROOT"), Var("RCX_RULESETS"),
                    Var("PROJECT_PATH"), Var("UNRELATED_PDK_STRING")]

        inputs = ()
        outputs = ()

    class Factory:
        @staticmethod
        def get(_step):
            return Target()

    class Config:
        def to_raw_dict(self):
            return {
                "PDK_ROOT": "/foss/pdks",
                "RCX_RULESETS": {"nom_*": "/foss/pdks/gf180mcuD/rules.rcx"},
                "PROJECT_PATH": str(project / "input"),
                "UNRELATED_PDK_STRING": "/foss/pdks/unrelated-value",
            }

    class Chip:
        gating_config_vars = {}
        factory = Factory()

        def __init__(self, **_kwargs):
            self.config = Config()

    fake_librelane = types.ModuleType("librelane")
    fake_flows = types.ModuleType("librelane.flows")
    fake_chip = types.ModuleType("librelane.flows.chip")
    fake_chip.Chip = Chip
    fake_steps = types.ModuleType("librelane.steps")
    fake_steps.Step = types.SimpleNamespace(factory=Factory())
    fake_version = types.ModuleType("librelane.__version__")
    fake_version.__version__ = "fixture"
    modules = {
        "librelane": fake_librelane,
        "librelane.flows": fake_flows,
        "librelane.flows.chip": fake_chip,
        "librelane.steps": fake_steps,
        "librelane.__version__": fake_version,
    }
    monkeypatch.setitem(sys.modules, "librelane", fake_librelane)
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def execute_resolver(cmd, **_kwargs):
        script = cmd[cmd.index("-c") + 1]
        assert "import json,sys,hashlib,os" in script
        argv = ["resolver.py", *cmd[cmd.index("-c") + 2:]]
        old = sys.argv
        try:
            sys.argv = argv
            exec(compile(script, "<generated-resolver>", "exec"), {})
        finally:
            sys.argv = old
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ll, "run_container", execute_resolver)
    design.parent.mkdir(parents=True)
    requested.write_text('["OpenROAD.RCX"]\n')
    configs = ll.resolve_step_configs(project, "fixture-image", "gf180mcuD",
                                      ["OpenROAD.RCX"], pdk_root=pdk_root,
                                      folder="generated")
    resolved = json.loads(configs["OpenROAD.RCX"].read_text())
    assert resolved["PDK_ROOT"] == "/pdk"
    assert resolved["RCX_RULESETS"]["nom_*"] == "/pdk/gf180mcuD/rules.rcx"
    assert resolved["PROJECT_PATH"] == str(project / "input")
    assert resolved["UNRELATED_PDK_STRING"] == "/foss/pdks/unrelated-value"


def _case(tmp_path):
    project = tmp_path / "project"
    pdk = tmp_path / "pdk-root" / "gf180mcuD"
    project.mkdir()
    pdk.mkdir(parents=True)
    rule = pdk / "rules.openrcx.gf180mcuD.nom"
    rule.write_text("nominal rule bytes\n")
    config = {
        "meta": {"step": "OpenROAD.RCX"},
        "PDK_ROOT": "/foss/pdks",
        "RCX_RULESETS": {"nom_*": "/foss/pdks/gf180mcuD/rules.openrcx.gf180mcuD.nom"},
        "TECH_LEFS": {"*": "/pdk/gf180mcuD/tech.lef"},
    }
    return project, pdk.parent, rule, config


def test_base_image_root_path_is_unreadable_under_declared_pdk_mount(tmp_path):
    project, pdk_root, _rule, config = _case(tmp_path)
    hashes = ll._sta_liberty_input_hashes(
        {"CELL_LIBS": {"*": [config["RCX_RULESETS"]["nom_*"]]}},
        project, [(pdk_root, "/pdk")])
    assert hashes[config["RCX_RULESETS"]["nom_*"]] is None


def test_step32_rcx_mapper_rebases_declared_image_root_to_pdk_mount(tmp_path):
    project, pdk_root, rule, config = _case(tmp_path)
    hashes = ll._rcx_pdk_input_hashes(config, project, [(pdk_root, "/pdk")])
    bound = "/pdk/gf180mcuD/rules.openrcx.gf180mcuD.nom"
    assert hashes[bound] == ll.digest(rule)


def test_step32_rcx_mapper_still_refuses_mutated_or_missing_rule(tmp_path):
    project, pdk_root, rule, config = _case(tmp_path)
    first = ll._rcx_pdk_input_hashes(config, project, [(pdk_root, "/pdk")])
    rule.write_text("mutated rule bytes\n")
    second = ll._rcx_pdk_input_hashes(config, project, [(pdk_root, "/pdk")])
    bound = "/pdk/gf180mcuD/rules.openrcx.gf180mcuD.nom"
    assert second[bound] != first[bound]
    rule.unlink()
    third = ll._rcx_pdk_input_hashes(config, project, [(pdk_root, "/pdk")])
    assert third[bound] is None


def test_step32_rcx_mapper_keeps_unknown_root_fail_closed(tmp_path):
    project, pdk_root, _rule, config = _case(tmp_path)
    config["RCX_RULESETS"] = {"nom_*": "/other-root/rules.rcx"}
    hashes = ll._rcx_pdk_input_hashes(config, project, [(pdk_root, "/pdk")])
    assert hashes["/other-root/rules.rcx"] is None
