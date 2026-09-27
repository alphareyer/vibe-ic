#!/usr/bin/env python3
"""Strict JSON and raw Git-object reads shared by the landing verifier's tools.

These primitives used to live inside the protected-path transition validator,
the two-step landing (PREPARE, then ACTIVATE) that the owner removed on
2026-09-27 ("remove all such two-step push! THAT IS USELESS!").  The validator
is gone; the readers that
only borrowed its strict-JSON and object-read helpers keep them here:
`landing_completion_record.py`, `hermetic_progress_emit.py` and
`trusted_test_selection.py`.

Nothing here decides a landing.  It parses, serialises and reads objects, and
refuses anything it cannot read exactly.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

MAX_JSON_BYTES = 1024 * 1024
HEX_RE = re.compile(r"[0-9a-f]+\Z")


class Refusal(RuntimeError):
    """The input cannot be read or measured exactly."""


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in out:
            raise Refusal(f"duplicate or non-string JSON key {key!r}")
        out[key] = value
    return out


def _reject_constant(value: str) -> Any:
    raise Refusal(f"non-finite JSON number {value!r}")


def strict_loads(raw: bytes | str, *, what: str) -> Any:
    if isinstance(raw, bytes):
        if len(raw) > MAX_JSON_BYTES:
            raise Refusal(f"{what} exceeds {MAX_JSON_BYTES} bytes")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise Refusal(f"{what} is not strict UTF-8") from exc
    else:
        text = raw
        if len(text.encode("utf-8")) > MAX_JSON_BYTES:
            raise Refusal(f"{what} exceeds {MAX_JSON_BYTES} bytes")
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise Refusal(f"{what} is not strict JSON: {exc}") from exc


def canonical_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise Refusal(f"record is not canonically serialisable: {exc}") from exc
    return (text + "\n").encode("utf-8")


def _safe_path(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise Refusal(f"{what} is not a safe repository path")
    pure = PurePosixPath(value)
    if (pure.is_absolute() or value != pure.as_posix()
            or any(part in {"", ".", ".."} for part in pure.parts)):
        raise Refusal(f"{what} is not a canonical relative path: {value!r}")
    return value


def _oid(value: Any, oid_len: int, what: str) -> str:
    if (not isinstance(value, str) or len(value) != oid_len
            or HEX_RE.fullmatch(value) is None):
        raise Refusal(f"{what} is not a lowercase {oid_len}-hex object id")
    return value


def _git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env.update({"GIT_NO_REPLACE_OBJECTS": "1", "LC_ALL": "C"})
    return env


def _git(repo: Path, args: Sequence[str], *, binary: bool = False) -> bytes | str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=_git_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise Refusal(f"git {' '.join(args[:2])} failed: {detail[:240]}")
    if binary:
        return proc.stdout
    try:
        return proc.stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise Refusal("git returned non-ASCII object metadata") from exc


def _object_format(repo: Path) -> tuple[str, int]:
    name = _git(repo, ["rev-parse", "--show-object-format"])
    if name == "sha1":
        return name, 40
    if name == "sha256":
        return name, 64
    raise Refusal(f"unsupported Git object format {name!r}")


def _commit_and_tree(repo: Path, rev: str, oid_len: int,
                     what: str) -> tuple[str, str]:
    commit = _oid(_git(repo, ["rev-parse", f"{rev}^{{commit}}"]),
                  oid_len, f"{what} commit")
    tree = _oid(_git(repo, ["rev-parse", f"{commit}^{{tree}}"]),
                oid_len, f"{what} tree")
    return commit, tree


def _tree(repo: Path, commit: str, oid_len: int
          ) -> dict[str, tuple[str, str]]:
    raw = _git(repo, ["ls-tree", "-rz", "--full-tree", commit], binary=True)
    assert isinstance(raw, bytes)
    entries: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            meta, raw_path = record.split(b"\t", 1)
            raw_mode, kind, raw_oid = meta.split()
            mode = raw_mode.decode("ascii")
            entry_kind = kind.decode("ascii")
            oid = raw_oid.decode("ascii")
            path = os.fsdecode(raw_path)
        except (ValueError, UnicodeDecodeError) as exc:
            raise Refusal("malformed ls-tree record") from exc
        _safe_path(path, "tree entry")
        _oid(oid, oid_len, f"tree entry {path}")
        if entry_kind != "blob" or mode not in {"100644", "100755", "120000"}:
            raise Refusal(f"unsupported tree entry {path!r} ({mode} {entry_kind})")
        if path in entries:
            raise Refusal(f"duplicate tree path {path!r}")
        entries[path] = (mode, oid)
    if not entries:
        raise Refusal("commit tree is empty")
    return entries


def _blob_oid(data: bytes, algorithm: str) -> str:
    digest = hashlib.sha1() if algorithm == "sha1" else hashlib.sha256()
    digest.update(f"blob {len(data)}\0".encode("ascii"))
    digest.update(data)
    return digest.hexdigest()


def _observe_file(repo: Path, path: str, entry: tuple[str, str],
                  algorithm: str, oid_len: int) -> dict[str, Any]:
    mode, oid = entry
    if mode not in {"100644", "100755"}:
        raise Refusal(f"path is not a regular file: {path}")
    data = _git(repo, ["cat-file", "blob", oid], binary=True)
    assert isinstance(data, bytes)
    if _blob_oid(data, algorithm) != oid:
        raise Refusal(f"raw blob object disagrees with its id: {path}")
    return {"path": path, "mode": mode, "blob_oid": oid,
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
