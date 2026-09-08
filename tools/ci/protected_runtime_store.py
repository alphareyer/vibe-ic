#!/usr/bin/env python3
"""Trusted-host runtime activation. Never mounted into a candidate namespace.

Only the operator commands write state. The verifier uses the fixed DEFAULT_STORE
and has no environment/CLI override. UID 65534 cannot own or access this store.
An approved runtime is a complete locally staged bundle, selected by exact digest;
neither a candidate manifest nor a preflight report grants activation authority.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path


DEFAULT_STORE = Path("/var/lib/vibeic/landing-runtime")
RECEIPT_KIND = "vibeic.controller-runtime-subject-receipt"


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"_controller_{name}", Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


snapshot = _load("protected_runtime_snapshot")
transition = snapshot.transition
attester = snapshot.attester
Refusal = snapshot.Refusal


def _private_store(store: Path) -> Path:
    if not store.is_absolute() or store.resolve() != store:
        raise Refusal("controller store must be an absolute path without symlinks")
    info = store.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_uid == 65534 or info.st_mode & 0o077):
        raise Refusal("controller store must be private to the trusted host operator")
    for parent in store.parents:
        mode = parent.stat()
        # /tmp is allowed for isolated tests, but sticky ownership prevents
        # replacement of this operator's private child. Production uses /var/lib.
        if mode.st_mode & 0o022 and not mode.st_mode & stat.S_ISVTX:
            raise Refusal("controller store has a writable non-sticky ancestor")
    return store


@contextlib.contextmanager
def _file_locked(path: Path, *, exclusive: bool):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise Refusal("controller lock is not operator-owned")
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def _locked(store: Path, *, exclusive: bool):
    _private_store(store)
    with _file_locked(store / "lock", exclusive=exclusive):
        yield


@contextlib.contextmanager
def _initial_store(store: Path):
    """Expose initial state only after validation and publication both succeed."""
    if not store.is_absolute() or store.resolve() != store:
        raise Refusal("controller store must be an absolute path without symlinks")
    # The sibling lock serializes provisioning before the store exists. Keep it
    # across attempts: unlinking a flock file would permit two different locks.
    with tempfile.TemporaryDirectory(prefix=f".{store.name}-init-", dir=store.parent) as temp:
        staged = Path(temp) / "store"
        staged.mkdir(mode=0o700)
        _private_store(staged)
        with _file_locked(store.with_name(f".{store.name}.initialize.lock"), exclusive=True):
            if store.exists() or store.is_symlink():
                raise Refusal("controller store already exists")
            yield staged
            os.rename(staged, store)
            directory = os.open(store.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)


def _read_active(store: Path) -> dict:
    path = store / "active.json"
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or info.st_mode & 0o077):
        raise Refusal("active runtime record is not operator-private")
    value = transition.strict_loads(path.read_bytes(), what="controller active runtime")
    transition._exact_keys(value, {"schema", "generation", "runtime_sha256", "history"}, "active runtime")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise Refusal("unsupported controller state schema")
    if type(value["generation"]) is not int or value["generation"] < 1:
        raise Refusal("invalid controller runtime generation")
    transition._sha256_value(value["runtime_sha256"], "active runtime digest")
    history = value["history"]
    if (not isinstance(history, list) or len(history) != value["generation"]
            or len(set(history)) != len(history) or history[-1] != value["runtime_sha256"]):
        raise Refusal("controller history is incomplete or permits a downgrade")
    for digest in history:
        transition._sha256_value(digest, "controller historical identity")
    return value


def active(store: Path = DEFAULT_STORE) -> dict:
    with _locked(store, exclusive=False):
        return _read_active(store)


def _publish_state(store: Path, state: dict):
    raw = transition.canonical_bytes(state)
    fd, name = tempfile.mkstemp(prefix=".active-", dir=store)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, store / "active.json")
        directory = os.open(store, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def activate(*, store: Path, bundle: Path, expected_current: str,
             expected_runtime_sha256: str, initialize: bool = False) -> dict:
    """Deliberate trusted-operator operation; no verifier can call it implicitly.

    Initial provisioning installs a separately trusted controller copy. Later
    runtime switches never replace that bootstrap controller; replacing it is a
    separate operator deployment, not something candidate code can request.
    """
    transition._sha256_value(expected_runtime_sha256, "reviewed runtime digest")
    if initialize:
        if expected_current != "none":
            raise Refusal("initial provisioning requires expected-current none")
        with _initial_store(store) as staged:
            return _install(store=staged, bundle=bundle, expected_current=expected_current,
                            expected_runtime_sha256=expected_runtime_sha256, initialize=True)
    return _install(store=store, bundle=bundle, expected_current=expected_current,
                    expected_runtime_sha256=expected_runtime_sha256, initialize=False)


def _install(*, store: Path, bundle: Path, expected_current: str,
             expected_runtime_sha256: str, initialize: bool) -> dict:
    with _locked(store, exclusive=True):
        if initialize:
            previous = {"runtime_sha256": "none", "generation": 0, "history": []}
        else:
            previous = _read_active(store)
        if previous["runtime_sha256"] != expected_current:
            raise Refusal("active runtime changed since operator review")
        if expected_runtime_sha256 in previous["history"]:
            raise Refusal("runtime downgrade/replay is refused")
        with tempfile.TemporaryDirectory(prefix=".install-", dir=store) as temp:
            private = Path(temp)
            selected = snapshot.select_bundle(
                object_repo=bundle, bundle=bundle,
                active_runtime_sha256=expected_runtime_sha256, output=private / "validated")
            identity = selected["identity"]
            snapshot.preflight_tree(private / "validated", sorted(
                {row["path"] for row in transition.derived_paths()} |
                {row["path"] for row in identity["overlays"]}))
            # Copy before the second measurement, so a source edit cannot race
            # the approval switch. Only a fully verified private copy is stored.
            shutil.copytree(bundle, private / "bundle", symlinks=True)
            snapshot.select_bundle(object_repo=bundle, bundle=private / "bundle",
                                   active_runtime_sha256=expected_runtime_sha256,
                                   output=private / "rechecked")
            destination = store / "bundles" / expected_runtime_sha256
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            if destination.is_symlink():
                raise Refusal("runtime bundle destination is a symlink")
            if destination.exists():
                # A failed state publication may leave this exact, unselected
                # bundle behind. Under the same CAS lock, revalidate its bytes
                # before completing the switch; never overwrite or trust it by
                # filename alone. History above still refuses actual replay.
                snapshot.select_bundle(object_repo=destination, bundle=destination,
                    active_runtime_sha256=expected_runtime_sha256,
                    output=private / "installed-rechecked")
            else:
                os.rename(private / "bundle", destination)
            if initialize:
                # An entire reviewed tree preserves all bootstrap imports.
                os.rename(private / "rechecked", store / "controller")
            state = {"schema": 1, "generation": previous["generation"] + 1,
                     "runtime_sha256": expected_runtime_sha256,
                     "history": previous["history"] + [expected_runtime_sha256]}
            _publish_state(store, state)
    return state


def select(*, store: Path, output: Path, expected_runtime_sha256: str | None = None) -> dict:
    with _locked(store, exclusive=False):
        state = _read_active(store)
        digest = state["runtime_sha256"]
        if expected_runtime_sha256 is not None and digest != expected_runtime_sha256:
            raise Refusal("active runtime changed during verification")
        bundle = store / "bundles" / digest
        selected = snapshot.select_bundle(object_repo=bundle, bundle=bundle,
            active_runtime_sha256=digest, output=output)
        return {**selected, "generation": state["generation"],
                "object_repo": str(bundle / "objects.git")}


def attest_selected(*, store: Path, root: Path, digest: str) -> dict:
    """Recheck the active choice and every runtime byte before final judgment."""
    with tempfile.TemporaryDirectory(prefix="runtime-recheck-") as temp:
        selected = select(store=store, output=Path(temp) / "expected",
                          expected_runtime_sha256=digest)
        identity = selected["identity"]
        expected = attester._tree(Path(selected["object_repo"]), identity["runtime_commit"])
        attester._attest(root, expected)
        if snapshot._content_sha256(root, expected) != identity["tree_sha256"]:
            raise Refusal("selected runtime differs from exact approval")
        return selected


def subject_receipt(*, store: Path, object_repo: Path, base: str, candidate: str,
                    candidate_gates: Path, candidate_tests: Path,
                    runtime: Path, digest: str) -> dict:
    selected = attest_selected(store=store, root=runtime, digest=digest)
    _algorithm, oid_len = transition._object_format(object_repo)
    base_commit, base_tree = transition._commit_and_tree(object_repo, base, oid_len, "product base")
    candidate_commit, candidate_tree = transition._commit_and_tree(object_repo, candidate, oid_len, "product candidate")
    transition._attest_worktree(object_repo, candidate_gates, candidate_commit)
    transition._attest_worktree(object_repo, candidate_tests, candidate_commit)
    payload = {"operation": "CONTROLLER_RUNTIME", "base_commit": base_commit,
               "base_tree": base_tree, "candidate_commit": candidate_commit,
               "candidate_tree": candidate_tree, "runtime_sha256": digest,
               "runtime_commit": selected["identity"]["runtime_commit"],
               "runtime_tree": selected["identity"]["runtime_tree"],
               "runtime_content_sha256": selected["identity"]["tree_sha256"],
               "generation": selected["generation"]}
    return {"schema": 1, "kind": RECEIPT_KIND, "complete": True,
            "payload": payload,
            "payload_sha256": hashlib.sha256(transition.canonical_bytes(payload)).hexdigest()}


def approved_verifier(store: Path, verifier: Path | None = None) -> Path:
    state = active(store)
    root = store / "bundles" / state["runtime_sha256"] / "tree"
    path = root / "tools/gatekeeper-verify-merge.sh"
    if verifier is not None and verifier.absolute() != path:
        raise Refusal("running verifier is not the externally approved runtime entry")
    attest_selected(store=store, root=root, digest=state["runtime_sha256"])
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--verifier", type=Path, required=True)
    dispatch = sub.add_parser("verify")
    dispatch.add_argument("args", nargs=argparse.REMAINDER)
    operator = sub.add_parser("activate")
    operator.add_argument("--store", type=Path, default=DEFAULT_STORE)
    operator.add_argument("--bundle", type=Path, required=True)
    operator.add_argument("--expected-current", required=True)
    operator.add_argument("--expected-runtime-sha256", required=True)
    operator.add_argument("--initialize", action="store_true")
    choose = sub.add_parser("select")
    choose.add_argument("--output", type=Path, required=True)
    choose.add_argument("--expected-runtime-sha256")
    check = sub.add_parser("attest")
    check.add_argument("--runtime", type=Path, required=True)
    check.add_argument("--expected-runtime-sha256", required=True)
    receipt = sub.add_parser("subject-receipt")
    for name in ("object-repo", "candidate-gates", "candidate-tests", "runtime", "record"):
        receipt.add_argument(f"--{name}", type=Path, required=True)
    for name in ("base", "candidate", "expected-runtime-sha256"):
        receipt.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "bootstrap":
            approved_verifier(DEFAULT_STORE, args.verifier)
            print("[PASS] approved controller entry")
            return 0
        if args.command == "verify":
            verifier = approved_verifier(DEFAULT_STORE)
            forwarded = args.args[1:] if args.args[:1] == ["--"] else args.args
            os.execv("/bin/bash", ["bash", str(verifier), *forwarded])
        elif args.command == "activate":
            value = activate(store=args.store, bundle=args.bundle,
                expected_current=args.expected_current,
                expected_runtime_sha256=args.expected_runtime_sha256, initialize=args.initialize)
        elif args.command == "select":
            value = select(store=DEFAULT_STORE, output=args.output,
                           expected_runtime_sha256=args.expected_runtime_sha256)
            print(value["runtime_sha256"], value["identity"]["runtime_commit"],
                  value["object_repo"], sep="\t")
            return 0
        elif args.command == "attest":
            value = attest_selected(store=DEFAULT_STORE, root=args.runtime,
                                    digest=args.expected_runtime_sha256)
        else:
            value = subject_receipt(store=DEFAULT_STORE, object_repo=args.object_repo,
                base=args.base, candidate=args.candidate,
                candidate_gates=args.candidate_gates, candidate_tests=args.candidate_tests,
                runtime=args.runtime, digest=args.expected_runtime_sha256)
            snapshot._atomic_write(args.record, transition.canonical_bytes(value))
    except (OSError, ValueError, Refusal, transition.Refusal, attester.Refusal) as exc:
        print(f"[NORECORD] controller runtime: {exc}", file=sys.stderr)
        return 2
    print("[PASS] controller runtime", args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
