"""F2 frontend production boundary. ENFORCEMENT: blocking on refused adoption.

The frozen controller owns run/choice/generation authority. This module owns
lexical input freshness and the canonical frontend import transaction. It does
not change the controller, portfolio, shared protocol or phase runners.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import json
import os
from pathlib import Path
import stat
import sys
from types import MappingProxyType
from typing import Mapping

_PROGRAMS_DIR = str(Path(__file__).resolve().parent)
if _PROGRAMS_DIR not in sys.path:
    sys.path.insert(0, _PROGRAMS_DIR)
import execution_modes as em

STEP_IDS = ("D1", "0.5ic", "1", "2", "3", "4", "5", "6", "7", "8",
            "10", "11", "FS1", "DT1", "12", "13", "DT2", "DT3", "P0")
PROGRAMS = Path(__file__).resolve().parent


def stable(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def lexical_tree(root: Path, prefix: str = "project", *,
                 roots: tuple[str, ...] = ("",), exclusions: tuple[str, ...] = ()):
    """Freeze names, empty/missing directories, aliases and resolved bytes.

    Traversal uses lexical names, so equal-byte alias retargeting is observable.
    Every resolved entry must stay under the declared boundary. Special files,
    unresolved links and cycles are refused before any worker starts.
    """
    root = Path(root).absolute()
    boundary = root.resolve(strict=root.exists() or root.is_symlink())
    files: dict[str, Path] = {}
    population: dict[str, object] = {}

    def excluded(rel: str) -> bool:
        return any(rel == p.rstrip("/") or rel.startswith(p.rstrip("/") + "/")
                   if not any(c in p for c in "*?[") else fnmatch.fnmatchcase(rel, p)
                   for p in exclusions)

    def visit(path: Path, rel: str, ancestors: tuple[Path, ...]):
        if excluded(rel):
            return
        name = prefix + ("/" + rel if rel else "")
        try:
            entry = path.lstat()
        except FileNotFoundError:
            population[name] = {"kind": "missing", "path": str(path)}
            return
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise em.Refusal("FRONTEND_INPUT_UNRESOLVED", str(path)) from exc
        if not resolved.is_relative_to(boundary):
            raise em.Refusal("FRONTEND_INPUT_ESCAPE", str(path))
        item = {"path": str(path), "resolved": str(resolved),
                "alias": os.readlink(path) if stat.S_ISLNK(entry.st_mode) else None}
        population[name] = item
        if resolved.is_dir():
            if resolved in ancestors:
                raise em.Refusal("FRONTEND_INPUT_CYCLE", str(path))
            item["kind"] = "directory"
            for child in sorted(path.iterdir()):
                visit(child, rel + "/" + child.name if rel else child.name,
                      (*ancestors, resolved))
        elif resolved.is_file():
            item.update(kind="file", sha256=em.digest(resolved))
            files[name] = resolved
        else:
            raise em.Refusal("FRONTEND_INPUT_NOT_REGULAR", str(path))

    for rel in roots:
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise em.Refusal("FRONTEND_UNSAFE_INPUT_ROOT", rel)
        visit(root / rel, rel, ())
    return files, population


@dataclass(frozen=True)
class FrontendContext(em.Context):
    project: Path = Path(".")
    roots: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    population: str = ""
    resources: tuple[tuple[str, str], ...] = ()
    sources: tuple[tuple[str, str], ...] = ()
    external_populations: tuple[tuple[str, str, str], ...] = ()
    # Historical upstream journal bytes are an immutable snapshot input;
    # the append-only canonical journal becomes an owned output. Its live
    # checkpoint changes only after the exact selected CAS commit.
    journal_current: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def source_files(self) -> Mapping[str, str]:
        """F1's exact source manifest, exposed without a second mutable map."""
        return MappingProxyType(dict(self.sources))

    def binding(self) -> dict:
        if not self.roots:
            raise em.Refusal('FRONTEND_STEP_INPUT_ROOTS_MISSING', self.step_id)
        _, current = lexical_tree(self.project, roots=self.roots,
                                  exclusions=self.exclusions)
        if stable(current) != self.population:
            raise em.Refusal("FRONTEND_INPUT_POPULATION_CHANGED", str(self.project))
        for prefix, root, expected in self.external_populations:
            _, actual = lexical_tree(Path(root), prefix)
            if stable(actual) != expected:
                raise em.Refusal("FRONTEND_EXTERNAL_POPULATION_CHANGED", root)
        for rel, expected in self.journal_current.items():
            path = self.project/rel
            actual = 'ABSENT' if not path.exists() else em.digest(path)
            if path.is_symlink() or actual != expected:
                raise em.Refusal('FRONTEND_UPSTREAM_JOURNAL_CHANGED', rel)
        for name, expected in (*self.sources, *self.resources):
            path = Path(name)
            if not expected or path.is_symlink() or not path.is_file() or em.digest(path) != expected:
                raise em.Refusal("FRONTEND_SOURCE_OR_RESOURCE_CHANGED", name)
        return super().binding()


def prepare(request):
    """Shared typed API; implementation dispatch belongs to the F2 worker."""
    from execution_step_protocol import PreparedStep
    from execution_frontend_worker import prepare_frontend
    if request.step_id not in STEP_IDS:
        raise em.Refusal("FRONTEND_STEP_NOT_OWNED", request.step_id)
    return prepare_frontend(request, PreparedStep)


def classify(request):
    """Source-only disposition pass; resource admission happens later."""
    from execution_step_protocol import PreparedStep
    from execution_frontend_worker import prepare_frontend
    if request.step_id not in STEP_IDS:
        raise em.Refusal("FRONTEND_STEP_NOT_OWNED", request.step_id)
    return prepare_frontend(request, PreparedStep, classification=True)


def consume(project: Path, context: FrontendContext, controller: em.Controller,
            run: Path, adopted: Mapping[str, object]) -> dict:
    from execution_frontend_worker import consume_frontend
    return consume_frontend(project, context, controller, run, adopted)
