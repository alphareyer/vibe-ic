#!/usr/bin/env python3
"""Materialize the one BASE-authorised runtime used by both landing arms."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_vibeic_{name}", Path(__file__).resolve().with_name(f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(f"trusted sibling {name} is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


transition = _load_sibling("protected_landing_transition")
attester = _load_sibling("trusted_worktree_attest")

SCHEMA = 1
KIND = "vibeic.protected-runtime-snapshot"
BUNDLE_KIND = "vibeic.reviewable-runtime-bundle"
PROGRAMS = "vibe-ic-marketplace/plugins/vibe-ic/programs"
PIN_CHECKER = f"{PROGRAMS}/ci_harness_timeout_ceiling_check.py"


class Refusal(RuntimeError):
    pass


def _pin_checker():
    # Execute the installed verifier's checker, never the staged candidate's
    # Python. Future pins are parsed as literals by that trusted implementation.
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "_runtime_preflight_pin_checker", root / PIN_CHECKER)
    if spec is None or spec.loader is None:
        raise Refusal("installed runtime pin checker is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preflight_tree(snapshot: Path, paths: Sequence[str]) -> dict[str, Any]:
    """BLOCKING local preparation checks; this is never an approval verdict.

    Preserve the entire BASE dependency tree. Check future pin literals using
    trusted code plus the existing semantic contract; no candidate JSON PASS or
    replacement checker can suppress this check. General runtime behaviour is
    still reviewed/tested by the controller before it approves this identity.
    """
    checker = _pin_checker()
    try:
        pins = checker.landing_pin_literals((snapshot / PIN_CHECKER).read_bytes())
        observed = checker.observe_landing_pins(snapshot)
        contract = checker.landing_semantic_progress_contract(snapshot, pins=pins)
    except (OSError, ValueError, SyntaxError) as exc:
        raise Refusal(f"runtime pin preflight cannot measure: {exc}") from exc
    if pins != observed or not contract["declared"] or contract["errors"]:
        raise Refusal(
            "runtime pin preflight: complete dependency/pin set is required "
            f"before PREPARE; include {PIN_CHECKER} with generated pins. "
            + "; ".join(contract["errors"]))
    syntax_paths = []
    for name in sorted(set(paths)):
        path = snapshot / name
        try:
            if path.suffix == ".py":
                compile(path.read_bytes(), name, "exec", dont_inherit=True)
                syntax_paths.append(name)
            elif path.suffix == ".sh":
                result = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-n", str(path)],
                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
                if result.returncode:
                    raise Refusal(f"runtime shell syntax failed: {name}: "
                                  + result.stderr.decode(errors="replace")[:500])
                syntax_paths.append(name)
        except (OSError, SyntaxError, subprocess.TimeoutExpired) as exc:
            raise Refusal(f"runtime syntax preflight cannot measure {name}: {exc}") from exc
    return {"check": "runtime-pins-and-syntax-v1", "pin_faces": 6,
            "semantic_lanes": len(contract["lanes"]), "syntax_paths": syntax_paths}


def _archive_base(repo: Path, commit: str, output: Path) -> dict:
    expected = attester._tree(repo, commit)
    # git archive is the existing snapshot transport. Attestation below catches
    # export-ignore/subst or unexpected archive contents instead of accepting a
    # filtered/partial dependency tree.
    raw = transition._git(repo, ["archive", "--format=tar", commit], binary=True)
    output.mkdir()
    # The private parent remains inaccessible on the host. The tree itself is
    # bind-mounted read-only for UID 65534 and must survive an operator umask 077.
    output.chmod(0o755)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        archive.extractall(output, filter="data")
    attester._attest(output, expected)
    return expected


def _overlay_records(repo: Path, expected: dict,
                     overlays: Mapping[str, Path]) -> list[dict[str, Any]]:
    _algorithm, oid_len = transition._object_format(repo)
    rows = []
    for name, source in sorted(overlays.items()):
        transition._safe_path(name, "runtime overlay")
        path = PurePosixPath(name)
        if ".git" in path.parts:
            raise Refusal("runtime overlay may not create Git control paths")
        if any(parent in expected for parent in path.parents):
            raise Refusal(f"runtime overlay traverses a tracked file/symlink: {name}")
        if any(path in entry.parents for entry in expected):
            raise Refusal(f"runtime overlay would replace a directory: {name}")
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise Refusal(f"runtime overlay is not a regular file: {name}")
        raw = source.read_bytes()
        mode = expected.get(path, (
            "100755" if info.st_mode & 0o111 else "100644", ""))[0]
        if mode not in {"100644", "100755"}:
            raise Refusal(f"runtime overlay may not replace a symlink: {name}")
        rows.append({"path": name, "mode": mode,
                     "blob_oid": attester._blob_digest(raw, oid_len),
                     "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)})
    return rows


def _content_sha256(root: Path, expected: dict) -> str:
    digest = hashlib.sha256()
    for path, (mode, _oid) in sorted(expected.items()):
        target = root / path
        raw = (os.fsencode(os.readlink(target)) if mode == "120000"
               else target.read_bytes())
        digest.update(transition.canonical_bytes({
            "path": str(path), "mode": mode, "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}))
    return digest.hexdigest()


def _snapshot_objects(root: Path, expected: dict, output: Path) -> tuple[str, str]:
    """Keep the complete runtime's selector provenance in its own object DB."""
    object_format = "sha256" if len(next(iter(expected.values()))[1]) == 64 else "sha1"
    subprocess.run(["git", "init", "--bare", f"--object-format={object_format}", "-q", str(output)],
                   env=attester._git_env(), check=True, capture_output=True)
    chunks = [b"commit refs/heads/runtime\n",
              b"committer Runtime bundle <runtime@invalid> 0 +0000\n",
              b"data 23\nFrozen runtime snapshot\n"]
    for path, (mode, _oid) in sorted(expected.items()):
        target = root / path
        raw = (os.fsencode(os.readlink(target)) if mode == "120000"
               else target.read_bytes())
        # Git's C-quoted UTF-8 pathname syntax is JSON-compatible here.
        import json
        quoted = json.dumps(str(path), ensure_ascii=False).encode()
        chunks += [b"M " + mode.encode() + b" inline " + quoted + b"\n",
                   f"data {len(raw)}\n".encode(), raw, b"\n"]
    chunks.append(b"\ndone\n")
    result = subprocess.run(["git", "-C", str(output), "fast-import", "--quiet"],
                            input=b"".join(chunks), env=attester._git_env(),
                            capture_output=True)
    if result.returncode:
        raise Refusal("cannot freeze runtime object provenance: " +
                      result.stderr.decode(errors="replace")[:400])
    commit = attester._git(output, "rev-parse", "refs/heads/runtime").decode().strip()
    tree = attester._git(output, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    attester._attest(root, attester._tree(output, commit))
    return commit, tree


def stage_bundle(*, object_repo: Path, base: str, overlays: Mapping[str, Path],
                 output: Path) -> dict[str, Any]:
    """Freeze a complete runtime for review; publish only after local preflight.

    `base` is a fixed runtime anchor. Product release commits are deliberately
    absent from this API. This function never registers/activates an identity.
    """
    repo = object_repo.resolve(strict=True)
    _algorithm, oid_len = transition._object_format(repo)
    commit, tree = transition._commit_and_tree(repo, base, oid_len, "runtime base")
    if output.exists() or output.is_symlink():
        raise Refusal("runtime bundle output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".runtime-stage-", dir=output.parent) as temp:
        private = Path(temp) / "bundle"
        private.mkdir()
        root = private / "tree"
        expected = _archive_base(repo, commit, root)
        rows = _overlay_records(repo, expected, overlays)
        for row in rows:
            # Copy the bytes just measured, then attest them against their OID
            # and SHA-256. A concurrently edited overlay invalidates staging.
            target = root / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            for parent in target.parents:
                if parent == root:
                    break
                parent.chmod(0o755)
            raw = overlays[row["path"]].read_bytes()
            if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise Refusal(f"runtime overlay changed during staging: {row['path']}")
            target.write_bytes(raw)
            target.chmod(0o755 if row["mode"] == "100755" else 0o644)
            expected[PurePosixPath(row["path"])] = (row["mode"], row["blob_oid"])
        attester._attest(root, expected)
        paths = sorted({row["path"] for row in transition.derived_paths()} |
                       {row["path"] for row in rows})
        preflight = preflight_tree(root, paths)
        runtime_commit, runtime_tree = _snapshot_objects(root, expected, private / "objects.git")
        identity = {"base_commit": commit, "base_tree": tree,
                    "runtime_commit": runtime_commit, "runtime_tree": runtime_tree,
                    "runner": transition.derived_runner(), "overlays": rows,
                    "tree_sha256": _content_sha256(root, expected)}
        record = {"schema": 1, "kind": BUNDLE_KIND, "identity": identity,
                  "runtime_sha256": runtime_identity(identity),
                  "preflight": preflight}
        (private / "bundle.json").write_bytes(transition.canonical_bytes(record))
        # Rename an already complete run-private directory. Nothing is emitted
        # at the public path while checks are pending or if they refuse.
        os.rename(private, output)
    return record


def runtime_identity(identity: Mapping[str, Any]) -> str:
    return transition.runtime_bundle_identity(identity)


def select_bundle(*, object_repo: Path, bundle: Path,
                  active_runtime_sha256: str, output: Path) -> dict[str, Any]:
    """Trusted-controller API: active digest MUST originate outside candidate.

    This argument is the controller's current exact choice, not a candidate
    request, a historic allowlist or a boolean approval. No file in the bundle
    can grant this authority. See INTERFACE.md for the host trust boundary.
    """
    transition._sha256_value(active_runtime_sha256, "controller active runtime")
    if bundle.is_symlink() or not bundle.is_dir():
        raise Refusal("runtime bundle is not a materialized directory")
    record = transition.strict_loads((bundle / "bundle.json").read_bytes(),
                                    what="runtime bundle")
    transition._exact_keys(record,
                           {"schema", "kind", "identity", "runtime_sha256", "preflight"},
                           "runtime bundle")
    if type(record["schema"]) is not int or record["schema"] != 1 or record["kind"] != BUNDLE_KIND:
        raise Refusal("runtime bundle schema/kind is unsupported")
    identity = transition._exact_keys(record["identity"],
        {"base_commit", "base_tree", "runtime_commit", "runtime_tree",
         "runner", "overlays", "tree_sha256"}, "runtime identity")
    digest = transition.require_active_runtime(
        identity, active_runtime_sha256=active_runtime_sha256)
    if record["runtime_sha256"] != digest:
        raise Refusal("runtime bundle digest does not match its identity")
    # Selection has no dependency on the product repository. The bundle keeps
    # its entire final tree in a separate immutable Git namespace.
    repo = bundle / "objects.git"
    if repo.is_symlink():
        raise Refusal("runtime object repository may not be a symlink")
    _algorithm, oid_len = transition._object_format(repo)
    commit = transition._oid(identity["runtime_commit"], oid_len, "runtime commit")
    if transition._commit_and_tree(repo, commit, oid_len, "runtime commit")[1] != identity["runtime_tree"]:
        raise Refusal("runtime tree differs from its identity")
    transition._runner_profile(identity["runner"])
    expected = attester._tree(repo, commit)
    rows = transition._state({"id": "runtime-overlays", "files": identity["overlays"]},
                             oid_len, "runtime overlays")["files"]
    for row in rows:
        path = PurePosixPath(row["path"])
        if ".git" in path.parts or any(parent in expected for parent in path.parents):
            raise Refusal("runtime overlay path crosses a file or Git control path")
        if expected.get(path) != (row["mode"], row["blob_oid"]):
            raise Refusal("runtime object tree differs from its overlay manifest")
    source = bundle / "tree"
    if source.is_symlink():
        raise Refusal("runtime tree may not be a symlink")
    attester._attest(source, expected)
    if _content_sha256(source, expected) != identity["tree_sha256"]:
        raise Refusal("runtime content digest differs from exact approval")
    if output.exists() or output.is_symlink():
        raise Refusal("selected runtime output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".runtime-select-", dir=output.parent) as temp:
        selected = Path(temp) / "tree"
        shutil.copytree(source, selected, symlinks=True)
        attester._attest(selected, expected)
        if _content_sha256(selected, expected) != identity["tree_sha256"]:
            raise Refusal("runtime changed while selecting approved bytes")
        os.rename(selected, output)
    return {"runtime_sha256": digest, "identity": dict(identity)}


def preflight_manifest(*, object_repo: Path, base: str,
                       manifest: Mapping[str, Any], overlays: Mapping[str, Path]) -> None:
    """Authoring boundary: refuse an unreachable next tuple before output."""
    repo = object_repo.resolve(strict=True)
    _algorithm, oid_len = transition._object_format(repo)
    commit, _tree = transition._commit_and_tree(repo, base, oid_len, "runtime base")
    with tempfile.TemporaryDirectory(prefix="runtime-manifest-preflight-") as temp:
        root = Path(temp) / "tree"
        expected = _archive_base(repo, commit, root)
        for move in manifest.get("moves", []):
            source, destination = PurePosixPath(move["from"]), PurePosixPath(move["to"])
            if any(parent in expected for parent in destination.parents):
                raise Refusal("runtime rename crosses a tracked file/symlink")
            target = root / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(root / source, target)
            expected[destination] = expected.pop(source)
        rows = _overlay_records(repo, expected, overlays)
        for row in rows:
            raw = overlays[row["path"]].read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise Refusal("runtime overlay changed during manifest preflight")
            target = root / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(0o755 if row["mode"] == "100755" else 0o644)
            expected[PurePosixPath(row["path"])] = (row["mode"], row["blob_oid"])
        for row in manifest["next"]["files"]:
            target = root / row["path"]
            if (hashlib.sha256(target.read_bytes()).hexdigest() != row["sha256"]
                    or expected[PurePosixPath(row["path"])][0] != row["mode"]):
                raise Refusal(f"staged runtime differs from manifest.next: {row['path']}")
        preflight_tree(root, [row["path"] for row in manifest["next"]["files"]])


def _expected_tree(repo: Path, receipt: Mapping[str, Any]
                   ) -> tuple[dict[PurePosixPath, tuple[str, str]], str,
                              list[dict[str, Any]]]:
    payload = receipt["payload"]
    operation = payload["operation"]
    if operation == "ACTIVATE":
        state_id = payload["candidate_state_id"]
        state_files = payload["candidate_files"]
    elif operation in {"STEADY", "PREPARE"}:
        state_id = payload["base_state_id"]
        state_files = payload["base_files"]
    else:  # parsed receipts already refuse this; retain a local hard boundary.
        raise Refusal("receipt has no executable runtime operation")
    expected = attester._tree(repo, payload["base_commit"])
    for row in state_files:
        path = PurePosixPath(row["path"])
        if path not in expected:
            raise Refusal(f"protected runtime path is absent from BASE: {path}")
        expected[path] = (row["mode"], row["blob_oid"])
    return expected, state_id, list(state_files)


def _write_blob(repo: Path, target: Path, row: Mapping[str, Any]) -> None:
    raw = transition._git(  # type: ignore[attr-defined]
        repo, ["cat-file", "blob", row["blob_oid"]], binary=True)
    assert isinstance(raw, bytes)
    if (len(raw) != row["size"]
            or hashlib.sha256(raw).hexdigest() != row["sha256"]):
        raise Refusal(f"protected blob disagrees with receipt: {row['path']}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.runtime.{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short protected-blob write")
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(fd)
    os.chmod(temporary, 0o755 if row["mode"] == "100755" else 0o644)
    os.replace(temporary, target)


def _snapshot_payload(receipt: Mapping[str, Any], state_id: str,
                      state_files: list[dict[str, Any]]) -> dict[str, Any]:
    payload = receipt["payload"]
    tuple_payload = {
        "operation": payload["operation"],
        "base_commit": payload["base_commit"],
        "base_tree": payload["base_tree"],
        "candidate_commit": payload["candidate_commit"],
        "candidate_tree": payload["candidate_tree"],
        "transition_id": payload["base_transition_id"],
        "runtime_state_id": state_id,
        "runner": payload["runner"],
        "protected_files": state_files,
    }
    return {
        "schema": SCHEMA,
        "kind": KIND,
        "complete": True,
        "payload": tuple_payload,
        "payload_sha256": hashlib.sha256(
            transition.canonical_bytes(tuple_payload)).hexdigest(),
    }


def validate(*, object_repo: Path, receipt: Mapping[str, Any],
             snapshot: Path) -> dict[str, Any]:
    repo = object_repo.resolve(strict=True)
    expected, state_id, state_files = _expected_tree(repo, receipt)
    try:
        root = snapshot.resolve(strict=True)
        if not root.is_dir() or root.is_symlink():
            raise Refusal("runtime snapshot is not a materialized directory")
        attester._attest(root, expected)
    except (OSError, attester.Refusal) as exc:
        raise Refusal(f"runtime raw-byte attestation failed: {exc}") from exc
    return _snapshot_payload(receipt, state_id, state_files)


def materialize(*, object_repo: Path, receipt: Mapping[str, Any],
                base_snapshot: Path, output: Path) -> dict[str, Any]:
    repo = object_repo.resolve(strict=True)
    payload = receipt["payload"]
    try:
        base_root = base_snapshot.resolve(strict=True)
        if not base_root.is_dir() or base_root.is_symlink():
            raise Refusal("BASE snapshot is not a materialized directory")
        expected_base = attester._tree(repo, payload["base_commit"])
        # The trusted verifier deliberately rematerializes BASE with
        # `git archive`, so its post-candidate authority snapshot has no mutable
        # .git control path.  Direct callers may instead provide a registered
        # detached worktree.  Attest either representation exactly; never ask a
        # plain archive for a control file it is intentionally forbidden to
        # carry.
        if (base_root / ".git").exists() or (base_root / ".git").is_symlink():
            attester._attest(
                base_root, expected_base, allow_git_control_file=True,
                object_repo=repo, expected_sha=payload["base_commit"])
        else:
            attester._attest(base_root, expected_base)
    except (OSError, attester.Refusal) as exc:
        raise Refusal(f"BASE snapshot is not object-exact: {exc}") from exc
    if output.exists() or output.is_symlink():
        raise Refusal("runtime output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(
            base_root, output, symlinks=True,
            ignore=lambda _directory, names: {".git"} if ".git" in names else set())
        # trusted-base-tools is parent-private (0700), while the sealed runtime
        # is consumed through a read-only bind by uid 65534.  Publish only
        # traverse/read permission on the root; no candidate gains host write
        # permission.
        os.chmod(output, 0o755)
        _expected, _state_id, state_files = _expected_tree(repo, receipt)
        for row in state_files:
            _write_blob(repo, output / row["path"], row)
        return validate(object_repo=repo, receipt=receipt, snapshot=output)
    except BaseException:
        # The caller gives this command a fresh, run-private path.  A partial
        # snapshot is never evidence; remove only that exact child on refusal.
        if output.exists() and output.parent != output:
            shutil.rmtree(output, ignore_errors=True)
        raise


def _atomic_write(path: Path, raw: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise Refusal("runtime record path already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short runtime-record write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage", help="complete local bundle; grants no approval")
    stage.add_argument("--object-repo", type=Path, required=True)
    stage.add_argument("--base", required=True, help="fixed runtime BASE anchor")
    stage.add_argument("--next-file", action="append", default=[], metavar="PATH=FILE")
    stage.add_argument("--output", type=Path, required=True)
    create = sub.add_parser("materialize")
    check = sub.add_parser("validate")
    for command in (create, check):
        command.add_argument("--object-repo", type=Path, required=True)
        command.add_argument("--receipt", type=Path, required=True)
        command.add_argument("--snapshot", type=Path, required=True)
        command.add_argument("--record", type=Path, required=True)
    create.add_argument("--base-snapshot", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "stage":
        try:
            overlays = {}
            for item in args.next_file:
                name, sep, source = item.partition("=")
                if not sep or not name or not source or name in overlays:
                    raise Refusal("--next-file must name each PATH=FILE exactly once")
                overlays[name] = Path(source)
            record = stage_bundle(object_repo=args.object_repo, base=args.base,
                                  overlays=overlays, output=args.output)
        except (OSError, ValueError, Refusal, transition.Refusal, attester.Refusal,
                subprocess.SubprocessError, tarfile.TarError) as exc:
            print(f"[NORECORD] runtime staging: {exc}", file=sys.stderr)
            return 2
        print(f"[STAGED] runtime {record['runtime_sha256']}; not authorized")
        return 0
    try:
        _algorithm, oid_len = transition._object_format(args.object_repo)
        receipt = transition.strict_load_receipt(args.receipt, oid_len=oid_len)
        if args.command == "materialize":
            record = materialize(
                object_repo=args.object_repo, receipt=receipt,
                base_snapshot=args.base_snapshot, output=args.snapshot)
        else:
            record = validate(
                object_repo=args.object_repo, receipt=receipt,
                snapshot=args.snapshot)
        _atomic_write(args.record, transition.canonical_bytes(record))
    except (OSError, Refusal, transition.Refusal, attester.Refusal) as exc:
        try:
            args.record.unlink()
        except OSError:
            pass
        print(f"[NORECORD] protected runtime snapshot: {exc}", file=sys.stderr)
        return 2
    print(f"[PASS] protected runtime snapshot: "
          f"{record['payload']['runtime_state_id']} "
          f"{record['payload_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
