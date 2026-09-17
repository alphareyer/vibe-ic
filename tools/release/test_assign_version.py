"""tools/release/test_assign_version.py — vibe-ic#2350.

`assign_version.py --next patch|minor` moves every DECLARED version position and
nothing else, and refuses (restoring the tree) when it cannot prove both halves.

Hermetic: each test builds a synthetic repo with the real layout — two
marketplace manifests, plugin.json, the three README prose sites — plus the
QUOTATIONS a tree-wide substitution would wrongly move (#2137): a docstring
citing the old version, a fixture JSON carrying it, a corpus cell name inside a
declared README, and mcp-eda's own separate version line.

Each verification layer is proven on its own: a mutated writer that defeats one
layer is run with the OTHER layers neutralised, so a redundant guard cannot hide
a dead one.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent / "assign_version.py"
_spec = importlib.util.spec_from_file_location("assign_version_under_test", TOOL)
av = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(av)

OLD = "1.21.6"

PLUGIN_REL = "vibe-ic-marketplace/plugins/vibe-ic"
DOCSTRING_REL = f"{PLUGIN_REL}/programs/real_ic_gate.py"
FIXTURE_REL = f"{PLUGIN_REL}/programs/tests/fixture_audit.json"
MCP_PKG_REL = f"{PLUGIN_REL}/mcp-eda/package.json"


def _json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def build_repo(root: Path, version: str = OLD, root_manifest_version=None) -> Path:
    _json(root / ".claude-plugin" / "marketplace.json", {
        "name": "vibe-ic-marketplace",
        "plugins": [{"name": "vibe-ic", "source": f"./{PLUGIN_REL}",
                     "version": root_manifest_version or version,
                     "keywords": ["ic-design", "asic"]}]})
    _json(root / "vibe-ic-marketplace" / ".claude-plugin" / "marketplace.json", {
        "name": "vibe-ic-marketplace",
        "plugins": [{"name": "vibe-ic", "source": "./plugins/vibe-ic",
                     "version": version}]})
    _json(root / PLUGIN_REL / ".claude-plugin" / "plugin.json",
          {"name": "vibe-ic", "version": version, "description": "d — x"})
    minor = ".".join(version.split(".")[:2])
    (root / "README.md").write_text(
        f"[![Plugin v{version}](https://img.shields.io/badge/plugin-v{version}-brightgreen.svg)](x)\n"
        f"[![MCP-EDA v1.0.0](https://img.shields.io/badge/mcp--eda-v1.0.0-brightgreen.svg)](y)\n"
        f"> **Status: v{minor} — mature.**\n"
        f"The published cell `ic/spm/v{version}_gf180mcuD` was produced by plugin {version}.\n")
    (root / "vibe-ic-marketplace" / "README.md").write_text(
        f"| Plugin version | **{version}** |\n"
        f"    └── vibe-ic/   ← the single plugin (v{version})\n"
        f"Measured on plugin {version}, main ed3965cc6.\n")
    (root / PLUGIN_REL / "README.md").write_text(
        f"# vibe-ic — AI-Native IC Design plugin (**v{version}**)\n")
    (root / DOCSTRING_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / DOCSTRING_REL).write_text(
        f'"""MEASURED on SPM, the published cell `ic/spm/v{version}_gf180mcuD`."""\n')
    _json(root / FIXTURE_REL, {"version": version, "run_at": "2026-09-15"})
    _json(root / MCP_PKG_REL, {"name": "mcp-eda", "version": "1.0.0"})
    return root


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


DECLARED = {
    ".claude-plugin/marketplace.json",
    "vibe-ic-marketplace/.claude-plugin/marketplace.json",
    f"{PLUGIN_REL}/.claude-plugin/plugin.json",
    "README.md", "vibe-ic-marketplace/README.md", f"{PLUGIN_REL}/README.md",
}


# -- GREEN: the bump ------------------------------------------------------------
@pytest.mark.parametrize("part,old,new", [
    ("minor", "1.21.6", "1.22.0"),
    ("patch", "1.21.6", "1.21.7"),
    ("patch", "1.21.99", "1.22.0"),     # the binding rollover
    ("minor", "1.21.99", "1.22.0"),
])
def test_next_version(part, old, new):
    assert av.next_version(old, part) == new


def test_minor_moves_every_declared_position_and_nothing_else(tmp_path):
    repo = build_repo(tmp_path)
    before = snapshot(repo)
    report, rc = av.assign(repo, "minor")
    assert rc == 0, report
    assert report["assigned"] == "1.22.0" and report["cadence"] == "FULL"
    after = snapshot(repo)

    moved = {k for k in before if before[k] != after[k]}
    assert moved == DECLARED

    for rel in (".claude-plugin/marketplace.json",
                "vibe-ic-marketplace/.claude-plugin/marketplace.json"):
        assert json.loads(after[rel])["plugins"][0]["version"] == "1.22.0"
    assert json.loads(after[f"{PLUGIN_REL}/.claude-plugin/plugin.json"])["version"] == "1.22.0"

    readme = after["README.md"].decode()
    assert "plugin-v1.22.0-" in readme and "[![Plugin v1.22.0]" in readme
    assert "**Status: v1.22 — mature.**" in readme
    # QUOTATIONS inside a declared file stay exactly as written
    assert f"`ic/spm/v{OLD}_gf180mcuD` was produced by plugin {OLD}." in readme
    assert "mcp--eda-v1.0.0" in readme
    assert f"Measured on plugin {OLD}, main ed3965cc6." in \
        after["vibe-ic-marketplace/README.md"].decode()


def test_a_stray_quotation_is_left_alone(tmp_path):
    repo = build_repo(tmp_path)
    before = snapshot(repo)
    _report, rc = av.assign(repo, "patch")
    assert rc == 0
    after = snapshot(repo)
    for rel in (DOCSTRING_REL, FIXTURE_REL, MCP_PKG_REL):
        assert after[rel] == before[rel], f"{rel} is a quotation and moved"


def test_dry_run_verifies_then_restores(tmp_path):
    repo = build_repo(tmp_path)
    before = snapshot(repo)
    report, rc = av.assign(repo, "minor", dry_run=True)
    assert rc == 0 and report["restored"] is True
    assert {c["file"] for c in report["changed"]} == DECLARED
    assert snapshot(repo) == before


def test_cli_runs_as_one_command(tmp_path):
    repo = build_repo(tmp_path)
    r = subprocess.run([sys.executable, str(TOOL), "--next", "minor",
                        "--repo", str(repo)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert f"assigned {OLD} -> 1.22.0" in r.stdout
    assert json.loads((repo / PLUGIN_REL / ".claude-plugin" / "plugin.json")
                      .read_text())["version"] == "1.22.0"


# -- RED: every refusal, each layer on its own ------------------------------------
def _neutralise_repo_checks(monkeypatch):
    """Turn off layer 3 (the repo's own sync + prose checks) so the independent
    expectation is the only thing that can refuse."""
    monkeypatch.setattr(av._pmd, "verify_synced", lambda *a, **k: (True, []))
    monkeypatch.setattr(av._prose, "audit", lambda *a, **k: ("PASS", [], {}))


def _manifests_only_writer(plugin_root, version):
    """A writer that MISSES the prose positions."""
    return av._pmd.write_version_all(plugin_root, version)


def test_a_missed_prose_position_is_refused_and_restored(tmp_path, monkeypatch):
    repo = build_repo(tmp_path)
    before = snapshot(repo)
    monkeypatch.setattr(av._gav, "_write_version", _manifests_only_writer)
    _neutralise_repo_checks(monkeypatch)
    report, rc = av.assign(repo, "minor")
    assert rc == av.RC_REFUSED
    joined = "\n".join(report["findings"])
    for rel in ("README.md", "vibe-ic-marketplace/README.md", f"{PLUGIN_REL}/README.md"):
        assert f"{rel}: prose is not exactly the declared claims moved" in joined
    assert snapshot(repo) == before


def test_a_missed_manifest_position_is_refused_and_restored(tmp_path, monkeypatch):
    repo = build_repo(tmp_path)
    before = snapshot(repo)
    real = av._gav._write_version

    def skips_root_manifest(plugin_root, version):
        root_mkt = repo / ".claude-plugin" / "marketplace.json"
        keep = root_mkt.read_bytes()
        av._pmd.write_version_all(plugin_root, version)
        av._prose.fix(repo, version)
        root_mkt.write_bytes(keep)
        return []

    monkeypatch.setattr(av._gav, "_write_version", skips_root_manifest)
    _neutralise_repo_checks(monkeypatch)
    report, rc = av.assign(repo, "minor")
    assert rc == av.RC_REFUSED, report
    assert any(".claude-plugin/marketplace.json: the document is not the old one"
               in f for f in report["findings"]), report["findings"]
    assert snapshot(repo) == before
    assert real is not skips_root_manifest


def test_a_touched_quotation_is_refused_and_restored(tmp_path, monkeypatch):
    """The #2137 lander: a tree-wide substitution of the old version."""
    repo = build_repo(tmp_path)
    before = snapshot(repo)
    real = av._gav._write_version

    def sed_everywhere(plugin_root, version):
        wrote = real(plugin_root, version)
        p = repo / DOCSTRING_REL
        p.write_text(p.read_text().replace(OLD, version))
        return wrote

    monkeypatch.setattr(av._gav, "_write_version", sed_everywhere)
    _neutralise_repo_checks(monkeypatch)
    report, rc = av.assign(repo, "minor")
    assert rc == av.RC_REFUSED
    assert any(f.startswith(f"{DOCSTRING_REL}: changed, and it is not a declared")
               for f in report["findings"]), report["findings"]
    assert snapshot(repo) == before


def test_a_quotation_inside_a_declared_readme_is_refused_and_restored(tmp_path, monkeypatch):
    repo = build_repo(tmp_path)
    before = snapshot(repo)
    real = av._gav._write_version

    def sed_the_readme(plugin_root, version):
        wrote = real(plugin_root, version)
        p = repo / "README.md"
        p.write_text(p.read_text().replace(OLD, version))
        return wrote

    monkeypatch.setattr(av._gav, "_write_version", sed_the_readme)
    _neutralise_repo_checks(monkeypatch)
    report, rc = av.assign(repo, "minor")
    assert rc == av.RC_REFUSED
    assert any(f.startswith("README.md: prose is not exactly") for f in report["findings"])
    assert snapshot(repo) == before


def test_a_reformatted_manifest_is_refused(tmp_path, monkeypatch):
    repo = build_repo(tmp_path)
    before = snapshot(repo)
    real = av._gav._write_version

    def reformats(plugin_root, version):
        wrote = real(plugin_root, version)
        p = repo / "vibe-ic-marketplace" / ".claude-plugin" / "marketplace.json"
        p.write_text(json.dumps(json.loads(p.read_text()), indent=4) + "\n")
        return wrote

    monkeypatch.setattr(av._gav, "_write_version", reformats)
    _neutralise_repo_checks(monkeypatch)
    report, rc = av.assign(repo, "minor")
    assert rc == av.RC_REFUSED
    assert any("vibe-ic-marketplace/.claude-plugin/marketplace.json" in f
               for f in report["findings"]), report["findings"]
    assert snapshot(repo) == before


def test_the_repo_checks_refuse_on_their_own(tmp_path, monkeypatch):
    """Layer 3 alone: the independent expectation is neutralised, and the repo's
    own sync + prose checks still refuse a writer that missed the prose."""
    repo = build_repo(tmp_path)
    before = snapshot(repo)
    monkeypatch.setattr(av._gav, "_write_version", _manifests_only_writer)
    monkeypatch.setattr(av, "_expected_prose", lambda text, new: text)
    report, rc = av.assign(repo, "minor")
    # the neutralised expectation now EXPECTS unmoved prose, so only layer 3 speaks
    assert rc == av.RC_REFUSED
    assert any(f.startswith("prose audit FINDINGS") for f in report["findings"])
    assert snapshot(repo) == before


def test_refuses_to_bump_from_a_drifted_state(tmp_path):
    repo = build_repo(tmp_path, root_manifest_version="1.21.5")
    before = snapshot(repo)
    report, rc = av.assign(repo, "minor")
    assert rc == av.RC_BAD_INPUT
    assert "already disagree" in report["error"]
    assert snapshot(repo) == before


def test_refuses_an_unparseable_version(tmp_path):
    repo = build_repo(tmp_path, version="one.two")
    report, rc = av.assign(repo, "patch")
    assert rc == av.RC_BAD_INPUT and "no parseable version" in report["error"]
