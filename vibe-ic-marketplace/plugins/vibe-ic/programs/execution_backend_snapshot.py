"""Backend INPUT population and transactional publication; no native dispatch."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_modes as em


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def relative(name: str) -> Path:
    p = Path(name)
    if not name or p.is_absolute() or any(x in ('..', '.') for x in p.parts):
        raise em.Refusal('BACKEND_UNSAFE_PATH', name)
    return p


def population(root: Path, prefix: str, inputs: dict, *, boundary: Path | None = None) -> dict:
    """Record lexical edges, directories, empty directories and resolved bytes.

    Aliases inside a root are permitted, but their identity is retained. The
    escape/cycle check happens before opening the alias target's bytes.
    """
    root = Path(os.path.abspath(root))
    boundary = Path(os.path.abspath(boundary or root))
    if boundary.is_symlink() or not boundary.is_dir():
        raise em.Refusal('BACKEND_INPUT_ROOT_UNSAFE', str(boundary))
    if not root.is_relative_to(boundary):
        raise em.Refusal('BACKEND_INPUT_ROOT_UNSAFE', str(root))
    if not root.exists() and not root.is_symlink():
        return {prefix: {'kind': 'missing', 'alias': None,
                         'resolved': str(root.relative_to(boundary))}}
    rows = {}

    def visit(path, name, ancestors):
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(boundary):
            raise em.Refusal('BACKEND_INPUT_ESCAPE', name)
        mode = path.lstat().st_mode
        edge = {'alias': os.readlink(path) if path.is_symlink() else None,
                'resolved': str(resolved.relative_to(boundary))}
        if resolved.is_dir():
            if resolved in ancestors:
                raise em.Refusal('BACKEND_INPUT_CYCLE', name)
            rows[name] = dict(edge, kind='directory')
            for child in sorted(path.iterdir()):
                visit(child, name + '/' + child.name, ancestors | {resolved})
        elif resolved.is_file() and stat.S_ISREG(resolved.stat().st_mode):
            rows[name] = dict(edge, kind='file', sha256=sha(resolved))
            inputs[name] = resolved
        else:
            raise em.Refusal('BACKEND_INPUT_NOT_REGULAR', name)

    try:
        visit(root, prefix, set())
    except (OSError, RuntimeError) as exc:
        raise em.Refusal('BACKEND_INPUT_UNREADABLE', str(exc)) from exc
    return rows


def file_input(path: Path, name: str, inputs: dict) -> dict:
    path = Path(os.path.abspath(path))
    if path.is_symlink() or not path.is_file():
        raise em.Refusal('BACKEND_INPUT_NOT_REGULAR', name)
    inputs[name] = path
    return {'kind': 'file', 'resolved': str(path), 'sha256': sha(path)}


def safe_target(project: Path, name: str) -> Path:
    target = project / relative(name)
    current = project
    for part in relative(name).parts:
        current /= part
        if current.is_symlink():
            raise em.Refusal('BACKEND_CANONICAL_ALIAS', name)
    if target.exists() and not target.is_file():
        raise em.Refusal('BACKEND_CANONICAL_NOT_FILE', name)
    return target


def adoption_target(name: str, paths: tuple[str, ...]) -> None:
    """Every canonical consume write must fit F1's declared journal roots."""
    target = relative(name)
    if not any(target == relative(root) or target.is_relative_to(relative(root)) for root in paths):
        raise em.Refusal('BACKEND_UNDECLARED_ADOPTION_PATH', name)


class Journal:
    """Capture bytes/modes before touching; restore deletions and creations too."""
    def __init__(self, project: Path):
        self.project = project
        self.before = {}
        self.directories = []

    def touch(self, name: str):
        p = safe_target(self.project, name)
        if name not in self.before:
            self.before[name] = ((p.read_bytes(), p.stat().st_mode & 0o777)
                                 if p.is_file() else None)
        return p

    def write(self, name: str, data: bytes):
        p = self.touch(name)
        missing = []
        parent = p.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir()
            self.directories.append(directory)
        # No truncation of an existing file before all source bytes are read.
        temporary = p.with_name(p.name + '.backend-transaction')
        if temporary.exists() or temporary.is_symlink():
            raise em.Refusal('BACKEND_TRANSACTION_COLLISION', str(temporary))
        try:
            temporary.write_bytes(data)
            temporary.replace(p)
        finally:
            temporary.unlink(missing_ok=True)

    def rollback(self):
        for name, previous in reversed(list(self.before.items())):
            p = safe_target(self.project, name)
            if previous is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(previous[0])
                p.chmod(previous[1])
        for directory in reversed(self.directories):
            try:
                directory.rmdir()
            except OSError:
                pass


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
