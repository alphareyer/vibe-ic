"""Bounded real-source lifecycle tests; no corpus, full suite or host activation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
LAND = "tools/gatekeeper-land.sh"
CHECKER = "vibe-ic-marketplace/plugins/vibe-ic/programs/ci_harness_timeout_ceiling_check.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


R = load(ROOT / "tools/ci/protected_runtime_snapshot.py", "bundle_snapshot_test")
P = R.transition
A = load(ROOT / "tools/ci/protected_landing_manifest_author.py", "bundle_author_test")
C = load(ROOT / CHECKER, "bundle_checker_test")
def store_module():
    # Keep the measured pre-fix author controls collectable on the real BASE.
    return load(ROOT / "tools/ci/protected_runtime_store.py", "bundle_store_test")


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], text=True,
                            capture_output=True, check=True)
    return result.stdout.strip()


def commit(repo, message):
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid",
        "-c", "core.hooksPath=/dev/null", "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    # Real checked-in authority sources, with a small synthetic product. The
    # fixture has the entire declared closure, not hollow placeholder scripts.
    paths = {row["path"] for row in P.derived_paths()} | {
        P.MANIFEST_PATH, "tools/ci/protected_runtime_store.py",
        "tools/ci/landing_execution_plan.py",
        "tools/ci/protected_landing_manifest_author.py"} | {
            f"vibe-ic-marketplace/plugins/vibe-ic/programs/{name}"
            for name in P.PUSH_PREFLIGHT_BASE_FILES}
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if (ROOT / path).exists():
            shutil.copy2(ROOT / path, target)
    (root / "feature.py").write_text("VALUE = 1\n")
    git(root, "init", "-q")
    commit(root, "initial runtime")
    return root


def shell_change(repo, tmp_path):
    future = tmp_path / "future.sh"
    future.write_bytes((repo / LAND).read_bytes().replace(
        b"#!/usr/bin/env bash\n", b"#!/usr/bin/env bash\n# pin dependency probe\n", 1))
    return future


def complete_overlays(repo, tmp_path):
    future = shell_change(repo, tmp_path)
    probe = tmp_path / "pin-input"
    (probe / "tools").mkdir(parents=True)
    shutil.copy2(future, probe / LAND)
    driver = Path(CHECKER).with_name("pytest_per_file_junit.py")
    (probe / driver).parent.mkdir(parents=True)
    shutil.copy2(repo / driver, probe / driver)
    checker = tmp_path / "repinned.py"
    checker.write_bytes(C.render_landing_pins(probe, (repo / CHECKER).read_bytes()))
    return {LAND: future, CHECKER: checker}


def stage(repo, tmp_path, *, overlays=None, name="bundle"):
    out = tmp_path / name
    record = R.stage_bundle(object_repo=repo, base="HEAD", overlays=overlays or {}, output=out)
    return out, record


def test_author_refuses_measured_pin_expansion_before_manifest_publication(repo, tmp_path):
    """Non-vacuous reverse control: old render returns a valid but unreachable tuple."""
    future = shell_change(repo, tmp_path)
    observation = "accepted"
    try:
        A.render(repo=repo, commit="HEAD", transition_id="pin-probe",
                 current_id="current", next_id="next", moves={LAND: future})
    except RuntimeError as exc:
        observation = str(exc)
    assert "runtime pin preflight" in observation, observation
    assert CHECKER in observation


def test_cli_refusal_writes_no_prepare_manifest(repo, tmp_path):
    future = shell_change(repo, tmp_path)
    out = tmp_path / "prepare.json"
    result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/ci/protected_landing_manifest_author.py"),
        "--repo", str(repo), "--commit", "HEAD", "--transition-id", "probe",
        "--current-id", "current", "--next-id", "next", "--next-file",
        f"{LAND}={future}", "--out", str(out)], capture_output=True, text=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "runtime pin preflight" in result.stderr
    assert not out.exists()


def test_stage_refuses_missing_pin_dependency_without_output(repo, tmp_path):
    with pytest.raises(RuntimeError, match="complete dependency/pin set"):
        stage(repo, tmp_path, overlays={LAND: shell_change(repo, tmp_path)})
    assert not (tmp_path / "bundle").exists()


def test_selected_runtime_readable_under_private_operator_umask(repo, tmp_path):
    source = tmp_path / "extra.py"
    source.write_text("VALUE = 1\n")
    previous = os.umask(0o077)
    try:
        bundle, record = stage(repo, tmp_path, overlays={"new/helper/extra.py": source})
        runtime = tmp_path / "selected"
        R.select_bundle(object_repo=repo, bundle=bundle,
            active_runtime_sha256=record["runtime_sha256"], output=runtime)
    finally:
        os.umask(previous)
    # These are the permissions visible through the runtime's read-only bind;
    # its containing controller/run directory stays host-private.
    for relative in (".", "new", "new/helper"):
        assert (runtime / relative).stat().st_mode & 0o005 == 0o005
    assert (runtime / "new/helper/extra.py").stat().st_mode & 0o004


def test_complete_fixture_prepare_activate_and_one_bundle_succeeds(repo, tmp_path):
    settled = A.render(repo=repo, commit="HEAD", transition_id="settled",
        current_id="settled-current", next_id="settled-next", moves={}, no_move=True)
    (repo / P.MANIFEST_PATH).write_bytes(A.serialise(settled))
    base = commit(repo, "settled fixture")
    overlays = complete_overlays(repo, tmp_path)
    manifest = A.render(repo=repo, commit=base, transition_id="one-complete",
        current_id="settled-next", next_id="one-complete-next", moves=overlays)
    assert [x["path"] for x, y in zip(manifest["next"]["files"], manifest["current"]["files"])
            if x != y] == sorted(overlays)
    (repo / P.MANIFEST_PATH).write_bytes(A.serialise(manifest))
    prepare = commit(repo, "one prepare")
    for name, source in overlays.items():
        (repo / name).write_bytes(source.read_bytes())
    activate = commit(repo, "one activate")
    operations = []
    for index, (old, new) in enumerate(((base, prepare), (prepare, activate))):
        gates, tests = tmp_path / f"gates-{index}", tmp_path / f"tests-{index}"
        git(repo, "worktree", "add", "-q", "--detach", str(gates), new)
        git(repo, "worktree", "add", "-q", "--detach", str(tests), new)
        receipt = P.build_receipt(object_repo=repo, base=old, candidate=new,
                                 candidate_gates=gates, candidate_tests=tests)
        operations.append(receipt["payload"]["operation"])
    assert operations == ["PREPARE", "ACTIVATE"]
    bundle, record = stage(repo, tmp_path)
    selected = R.select_bundle(object_repo=repo, bundle=bundle,
        active_runtime_sha256=record["runtime_sha256"], output=tmp_path / "selected")
    assert selected["runtime_sha256"] == record["runtime_sha256"]
    assert record["preflight"]["pin_faces"] == 6


@pytest.mark.parametrize("mutation", ["source", "dependency", "mode", "forged_record", "symlink"])
def test_changed_bytes_modes_dependencies_or_forged_approval_refuse(repo, tmp_path, mutation):
    bundle, record = stage(repo, tmp_path)
    if mutation in {"source", "dependency"}:
        path = bundle / "tree" / (LAND if mutation == "source" else "feature.py")
        path.write_bytes(path.read_bytes() + b"\n# changed after review\n")
    elif mutation == "mode":
        path = bundle / "tree/feature.py"
        path.chmod(path.stat().st_mode ^ 0o111)
    elif mutation == "symlink":
        path = bundle / "tree/feature.py"
        path.unlink()
        path.symlink_to(repo / "feature.py")
    else:
        record["identity"]["tree_sha256"] = "0" * 64
        record["runtime_sha256"] = R.runtime_identity(record["identity"])
        (bundle / "bundle.json").write_bytes(P.canonical_bytes(record))
    original = stage(repo, tmp_path, name="original")[1]["runtime_sha256"]
    with pytest.raises(RuntimeError):
        R.select_bundle(object_repo=repo, bundle=bundle,
                        active_runtime_sha256=original, output=tmp_path / "selected")
    assert not (tmp_path / "selected").exists()


def test_unapproved_bundle_cannot_choose_its_runtime(repo, tmp_path):
    bundle, _record = stage(repo, tmp_path)
    for digest in (None, "", "0" * 64):
        with pytest.raises(RuntimeError):
            R.select_bundle(object_repo=repo, bundle=bundle,
                            active_runtime_sha256=digest, output=tmp_path / "selected")


def test_product_releases_reuse_runtime_and_old_approval_cannot_downgrade(repo, tmp_path, monkeypatch):
    S = store_module()
    bundle, first = stage(repo, tmp_path)
    store = tmp_path / "store"
    with pytest.raises(RuntimeError):
        S.activate(store=store, bundle=bundle, expected_current="none",
                   expected_runtime_sha256="0" * 64, initialize=True)
    assert not store.exists(), "refused initialization must not disable the legacy entry"
    publish_state = S._publish_state

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as fault:
        fault.setattr(S, "_publish_state", disk_full)
        with pytest.raises(OSError, match="No space left"):
            S.activate(store=store, bundle=bundle, expected_current="none",
                       expected_runtime_sha256=first["runtime_sha256"], initialize=True)
    assert not store.exists(), "failed initial publication must remain privately staged"
    S.activate(store=store, bundle=bundle, expected_current="none",
               expected_runtime_sha256=first["runtime_sha256"], initialize=True)
    chosen1 = S.select(store=store, output=tmp_path / "product1-runtime")
    # A feature may even edit its own verifier; it cannot select those bytes.
    (repo / "feature.py").write_text("VALUE = 2\n")
    (repo / "tools/gatekeeper-verify-merge.sh").write_text("#!/bin/sh\nexit 0\n")
    commit(repo, "ordinary product feature")
    chosen2 = S.select(store=store, output=tmp_path / "product2-runtime")
    assert chosen1 == chosen2
    assert (tmp_path / "product2-runtime/tools/gatekeeper-verify-merge.sh").read_bytes() != \
        (repo / "tools/gatekeeper-verify-merge.sh").read_bytes()
    new_bundle, second = stage(repo, tmp_path, name="next-bundle")
    with pytest.raises(RuntimeError, match="changed since operator review"):
        S.activate(store=store, bundle=new_bundle, expected_current="0" * 64,
                   expected_runtime_sha256=second["runtime_sha256"])
    with monkeypatch.context() as fault:
        fault.setattr(S, "_publish_state", disk_full)
        with pytest.raises(OSError, match="No space left"):
            S.activate(store=store, bundle=new_bundle, expected_current=first["runtime_sha256"],
                       expected_runtime_sha256=second["runtime_sha256"])
    assert S.active(store)["runtime_sha256"] == first["runtime_sha256"]
    # A leftover directory is not approval: retry must attest it again.
    installed = store / "bundles" / second["runtime_sha256"] / "tree/feature.py"
    original = installed.read_bytes()
    installed.write_bytes(original + b"# changed after interrupted installation\n")
    with pytest.raises(RuntimeError):
        S.activate(store=store, bundle=new_bundle, expected_current=first["runtime_sha256"],
                   expected_runtime_sha256=second["runtime_sha256"])
    assert S.active(store)["runtime_sha256"] == first["runtime_sha256"]
    installed.write_bytes(original)
    assert S._publish_state is publish_state
    S.activate(store=store, bundle=new_bundle, expected_current=first["runtime_sha256"],
               expected_runtime_sha256=second["runtime_sha256"])
    with pytest.raises(RuntimeError, match="downgrade/replay"):
        S.activate(store=store, bundle=bundle, expected_current=second["runtime_sha256"],
                   expected_runtime_sha256=first["runtime_sha256"])
    with pytest.raises(RuntimeError, match="changed during verification"):
        S.select(store=store, output=tmp_path / "downgrade",
                 expected_runtime_sha256=first["runtime_sha256"])


def test_repin_preserves_structural_negative_control(repo, tmp_path):
    path = repo / LAND
    path.write_bytes(path.read_bytes().replace(b"-p no:cacheprovider", b"-p cacheprovider", 1))
    with pytest.raises(ValueError, match="cannot repin invalid semantic runtime"):
        C.render_landing_pins(repo, (repo / CHECKER).read_bytes())


def test_store_permissions_and_candidate_uid_are_not_authority(repo, tmp_path, monkeypatch):
    S = store_module()
    bundle, record = stage(repo, tmp_path)
    store = tmp_path / "store"
    S.activate(store=store, bundle=bundle, expected_current="none",
               expected_runtime_sha256=record["runtime_sha256"], initialize=True)
    store.chmod(0o755)
    with pytest.raises(RuntimeError, match="private"):
        S.active(store)
    store.chmod(0o700)
    monkeypatch.setattr(os, "geteuid", lambda: 65534)
    with pytest.raises(RuntimeError, match="private"):
        S.active(store)


def test_real_verifier_dispatches_approved_judge_not_product_base(repo, tmp_path):
    """Actual verifier CLI reaches the real final judge on a merge conflict.

    No gate/test arms or corpus run: conflict is the bounded terminal path. The
    product deliberately carries a poisonous verifier/judge, distinct from the
    reviewed runtime. Reaching a structured REFUSE proves final judge routing.
    """
    S = store_module()
    store = tmp_path / "controller-store"
    # Model host provisioning in a private fixture, never write /var/lib.
    for rel in ("tools/ci/protected_runtime_store.py", "tools/gatekeeper-verify-merge.sh"):
        path = repo / rel
        path.write_text(path.read_text().replace("/var/lib/vibeic/landing-runtime", str(store)))
    commit(repo, "fixture controller provision point")
    bundle, record = stage(repo, tmp_path)
    S.activate(store=store, bundle=bundle, expected_current="none",
               expected_runtime_sha256=record["runtime_sha256"], initialize=True)
    product = tmp_path / "product"
    (product / "tools").mkdir(parents=True)
    (product / LAND).write_text("#!/bin/sh\nexit 99\n")
    (product / "tools/gatekeeper-verify-merge.sh").write_text("#!/bin/sh\necho PRODUCT_VERIFIER_EXECUTED\nexit 99\n")
    judge = product / "vibe-ic-marketplace/plugins/vibe-ic/programs/landing_merge_verdict.py"
    judge.parent.mkdir(parents=True)
    judge.write_text("raise RuntimeError('PRODUCT_JUDGE_EXECUTED')\n")
    (product / "feature.py").write_text("VALUE = 0\n")
    git(product, "init", "-q")
    common = commit(product, "common fixture")
    (product / "feature.py").write_text("VALUE = 1\n")
    base = commit(product, "product base")
    git(product, "checkout", "-q", "--detach", common)
    (product / "feature.py").write_text("VALUE = 2\n")
    candidate = commit(product, "product feature")
    verdict = tmp_path / "verdict.json"
    helper = store / "controller/tools/ci/protected_runtime_store.py"
    env = dict(os.environ, VIBEIC_NDA_TOKENS=json.dumps({"sku_full": "synthetic-private-token"}))
    result = subprocess.run([sys.executable, "-I", "-B", str(helper), "verify", "--",
        "--repo", str(product), "--base", base, "--ref", candidate, "--no-fetch",
        "--json", str(verdict)], capture_output=True, text=True, timeout=60, env=env)
    # A conflict deliberately runs no arms, so the real judge also records
    # UNMEASURED protected evidence and returns its documented NORECORD rc=2.
    assert result.returncode == 2, result.stdout + result.stderr
    assert "PRODUCT_JUDGE_EXECUTED" not in result.stdout + result.stderr
    assert "PRODUCT_VERIFIER_EXECUTED" not in result.stdout + result.stderr
    parsed = json.loads(verdict.read_text())
    assert parsed["verdict"] != "LAND_OK"
    assert parsed["base_sha"] == base
    assert "REBASE CONFLICT" in result.stdout


@pytest.mark.parametrize("population_api", [True, False])
def test_approved_selector_uses_its_own_object_repo(repo, tmp_path, population_api):
    selection = load(ROOT / "tools/ci/trusted_test_selection.py", "bundle_selection_test")
    # Add the actual mandatory control tests, so the selector has a nonempty
    # population. No tests are executed by selection.
    for rel in selection.CONTROL_TESTS:
        path = repo / selection.PLUGIN_REL / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / selection.PLUGIN_REL / rel, path)
    included = "programs/tests/test_runtime_product.py"
    excluded = "programs/tests/test_runtime_fixture/test_runtime_product.py"
    deleted = "programs/tests/test_runtime_deleted.py"
    # Exercise the integrated population parser with an actual declaration.
    # Empty edge selection isolates the directly-changed-test union; the
    # negative arm explicitly removes the API instead of relying on old code
    # continuing not to implement it.
    (repo / selection.PLUGIN_REL / "pytest.ini").write_text(
        "[pytest]\ntestpaths = programs/tests\nnorecursedirs = test_runtime_fixture\n")
    selector = repo / selection.SELECTOR_REL
    selector.write_text(selector.read_text() + "\n" +
        "def select_tests(*args, **kwargs):\n    return []\n" +
        ("declared_test_population = None\n" if not population_api else ""))
    deleted_path = repo / selection.PLUGIN_REL / deleted
    deleted_path.write_text("def test_deleted():\n    assert True\n")
    base = commit(repo, "selection controls")
    bundle, record = stage(repo, tmp_path)
    runtime = tmp_path / "runtime"
    R.select_bundle(object_repo=repo, bundle=bundle,
        active_runtime_sha256=record["runtime_sha256"], output=runtime)
    # Poison the product selector; selection must use the approved implementation.
    (repo / selection.SELECTOR_REL).write_text("raise RuntimeError('product selector executed')\n")
    for rel in (included, excluded):
        target = repo / selection.PLUGIN_REL / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_product():\n    assert True\n")
    deleted_path.unlink()
    candidate = commit(repo, "product changes selector")
    base_root = tmp_path / "base"
    git(repo, "worktree", "add", "-q", "--detach", str(base_root), base)
    candidate_root = tmp_path / "candidate"
    git(repo, "worktree", "add", "-q", "--detach", str(candidate_root), candidate)
    args = dict(object_repo=repo, base=base, candidate=candidate,
        selector_commit=record["identity"]["runtime_commit"],
        selector_object_repo=bundle / "objects.git", selector_path=runtime / selection.SELECTOR_REL,
        base_snapshot=base_root, candidate_snapshot=candidate_root)
    if not population_api:
        with pytest.raises(RuntimeError, match="lacks declared_test_population API"):
            selection.build(**args)
        return
    result = selection.build(**args)
    assert result["payload"]["selector_commit"] == record["identity"]["runtime_commit"]
    assert set(selection.CONTROL_TESTS) <= set(result["payload"]["candidate_selection"])
    assert result["payload"]["base_commit"] == base
    assert result["payload"]["candidate_commit"] == candidate
    assert included in result["payload"]["candidate_selection"]
    assert excluded not in result["payload"]["candidate_selection"]
    assert deleted in result["payload"]["base_selection"]
    assert deleted not in result["payload"]["candidate_selection"]


def test_real_final_judge_consumes_controller_receipt_and_refuses_forgery(repo, tmp_path):
    S = store_module()
    store = tmp_path / "judge-store"
    helper_source = repo / "tools/ci/protected_runtime_store.py"
    helper_source.write_text(helper_source.read_text().replace(
        "/var/lib/vibeic/landing-runtime", str(store)))
    base = commit(repo, "fixture external authority")
    bundle, record = stage(repo, tmp_path)
    S.activate(store=store, bundle=bundle, expected_current="none",
               expected_runtime_sha256=record["runtime_sha256"], initialize=True)
    runtime = store / "bundles" / record["runtime_sha256"] / "tree"
    (repo / "feature.py").write_text("VALUE = 2\n")
    candidate = commit(repo, "ordinary feature")
    gates, tests = tmp_path / "gates", tmp_path / "tests"
    git(repo, "worktree", "add", "-q", "--detach", str(gates), candidate)
    git(repo, "worktree", "add", "-q", "--detach", str(tests), candidate)
    receipt = S.subject_receipt(store=store, object_repo=repo, base=base,
        candidate=candidate, candidate_gates=gates, candidate_tests=tests,
        runtime=runtime, digest=record["runtime_sha256"])
    path = tmp_path / "receipt.json"
    R._atomic_write(path, P.canonical_bytes(receipt))
    code = """
import importlib.util, json, pathlib, sys
path = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(path.parent))
spec = importlib.util.spec_from_file_location('approved_final_judge', path)
judge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = judge
spec.loader.exec_module(judge)
_receipt, summary, error = judge.read_protected_transition_receipt(
    sys.argv[2], base_commit=sys.argv[3], candidate_commit=sys.argv[4],
    base_tree=sys.argv[5], candidate_tree=sys.argv[6])
print(json.dumps({'summary': summary, 'error': error}))
"""
    command = [sys.executable, "-I", "-B", "-c", code,
        str(runtime / "vibe-ic-marketplace/plugins/vibe-ic/programs/landing_merge_verdict.py"),
        str(path), base, candidate, git(repo, "rev-parse", f"{base}^{{tree}}"),
        git(repo, "rev-parse", f"{candidate}^{{tree}}")]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    observed = json.loads(result.stdout)
    assert observed["error"] == "", observed
    assert observed["summary"]["runtime_sha256"] == record["runtime_sha256"]
    receipt["payload"]["runtime_commit"] = "0" * 40
    receipt["payload_sha256"] = hashlib.sha256(P.canonical_bytes(receipt["payload"])).hexdigest()
    path.write_bytes(P.canonical_bytes(receipt))
    forged = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
    assert "runtime provenance mismatch" in forged["error"]
    (gates / "feature.py").write_text("VALUE = 99\n")
    with pytest.raises(RuntimeError, match="raw attestation"):
        S.subject_receipt(store=store, object_repo=repo, base=base,
            candidate=candidate, candidate_gates=gates, candidate_tests=tests,
            runtime=runtime, digest=record["runtime_sha256"])
