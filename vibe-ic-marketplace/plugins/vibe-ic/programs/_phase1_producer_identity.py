#!/usr/bin/env python3
"""_phase1_producer_identity.py — are the generated L docs what THIS producer
would write? (FX_STALE_LDOCS)

THE MEASURED DEFECT
-------------------
The front door skipped Phase 1 whenever 13 L documents existed. So a Phase-1
PRODUCER fix never reached an existing project: MEASURED on subservient
(8HD-4, 2026-09-28, lane fxtb FX_P2 arm f4), an exact fresh copy of a tree
whose L1 had been written by the pre-fix producer kept FAILing
`l1_pin_table_aliases_typed_check` after the fix had landed in the producer;
only deleting `phase1/generated_docs` let the fix through (arm f5). A skip
keyed on "the files exist" instead of "these files are what the current
producer would write" is a stale cache.

The version stamp every L doc already carries (`_generator.plugin_version`)
could not have caught it: the pre-fix L1 and the fixed producer both said
1.25.64, because a version is assigned only at landing. What changed was CODE.

WHAT THIS RECORDS, AND WHERE
----------------------------
The flow's existing per-step identity, not a parallel record:
`_step_identity`'s sidecar format (`step_identity.json`, kind `phase1`, in
`phase1/`) and `_step_recorder`'s record format, filled at MODULE granularity
by `ProducerRecorder` (what phase 1 loaded and launched -- no profiler; see
its docstring for the measured 10x cost of the per-call recorder). Phase 1
runs inside `with ProducerRecorder(...)` and stamps, as its last act:

  code     digest of the recording (functions + module bodies it executed,
           plus plugin scripts it launched) — `code_from_recording`
  inputs   sha256 over the DESIGN INPUT it read (`input/**`, and the Path-B
           raw corpus `phase1/input_doc/**`)
  outputs  sha256 of every generated L doc, as phase 1 left it
  recording, plugin_version (for the reason a human reads)

WHAT THE FRONT DOOR DECIDES (`assess`)
--------------------------------------
  REUSE       every generated doc is byte-identical to what was stamped, the
              recorded code re-derives to the same digest from CURRENT source,
              and the design input is unchanged.
  REGENERATE  NO_PRODUCER_IDENTITY (an old project, or a stamp that could not
              be written), PRODUCER_CHANGED (old vs new code digest and plugin
              version named), DESIGN_INPUT_CHANGED, GENERATED_DOC_REMOVED.
  REFUSE      GENERATED_DOC_EDITED: a generated doc no longer has the bytes its
              producer recorded, or an L doc the producer never wrote is
              present. Neither silently overwritten nor silently reused as
              current; the reason names the two ways out.

INPUT documents are never judged here: they are the design input. A project
whose L docs have no design input behind them was HANDED its L docs, and the
caller keeps them (see `vibe_ic_one_shot_runner._phase1_decision`).

FAILS CLOSED like its parent: a component that cannot be established is a
mismatch, and a mismatch regenerates. Reuse needs positive proof.
chip-AGNOSTIC: paths and digests only.
"""
from __future__ import annotations

import os as _os
import sys as _sys

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import _path_layout as _pl
import _step_identity as _si

KIND = "phase1"
REUSE = "REUSE"
REGENERATE = "REGENERATE"
REFUSE = "REFUSE"

NO_PRODUCER_IDENTITY = "NO_PRODUCER_IDENTITY"
PRODUCER_CHANGED = "PRODUCER_CHANGED"
DESIGN_INPUT_CHANGED = "DESIGN_INPUT_CHANGED"
GENERATED_DOC_REMOVED = "GENERATED_DOC_REMOVED"
GENERATED_DOC_EDITED = "GENERATED_DOC_EDITED"

#: §4.05 — never hashed as design input, wherever they sit under input/.
_FORBIDDEN_PARTS = frozenset(
    {"golden", "oracle", "reference_flow", "harness"})


class ProducerRecorder:
    """Which plugin code phase 1 RAN, at MODULE granularity, with no profiler.

    MEASURED (subservient E2E, 8HD-4): phase 1 under `_step_recorder.Recorder`
    -- a `sys.setprofile` hook on every call -- took ~200 s against ~20 s
    without it. Phase 1 is cheap to regenerate, so function granularity buys
    nothing worth a 10x tax on every run: any edit to a module phase 1 loaded
    regenerates it, which costs ~20 s and can never reuse a stale doc.

    RECORDS what the interpreter actually loaded -- every module in
    `sys.modules` whose file is under the plugin root when phase 1 finishes,
    which includes `importlib` / lazy imports -- plus every plugin SCRIPT
    phase 1 launched out of process (watched at `subprocess.Popen`, the one
    place every launch passes, as the step recorder does) and that script's
    static import closure. Emitted in the step recorder's own whole-file entry
    form (`launched:<rel>` -> `{"__whole__": sha256}`), so
    `_step_identity.code_from_stored` re-derives and compares it unchanged.
    Fails closed: nothing loaded, or an unreadable file, records nothing."""

    def __init__(self, root: Path):
        self._root = Path(root).resolve()
        self._launched: set = set()
        self._prev_popen = None

    def __enter__(self) -> "ProducerRecorder":
        import subprocess
        self._prev_popen = subprocess.Popen
        rec = self

        class _WatchedPopen(self._prev_popen):  # type: ignore[misc]
            def __init__(self, args, *a, **k):
                rec._note(args)
                super().__init__(args, *a, **k)
        subprocess.Popen = _WatchedPopen   # type: ignore[assignment]
        return self

    def __exit__(self, *_exc) -> None:
        import subprocess
        if self._prev_popen is not None:
            subprocess.Popen = self._prev_popen  # type: ignore[assignment]

    def _note(self, argv: Any) -> None:
        import _step_recorder as _sr
        try:
            text = argv if isinstance(argv, str) else " ".join(
                str(x) for x in argv)
        except Exception:                                  # noqa: BLE001
            return
        self._launched.update(_sr._SCRIPT_RE.findall(text))

    def _under_root(self, f: Any) -> Optional[Path]:
        try:
            q = Path(str(f)).resolve()
        except (OSError, ValueError):
            return None
        if q.suffix != ".py" or not q.is_file():
            return None
        try:
            q.relative_to(self._root)
        except ValueError:
            return None
        return q

    def recorded(self):
        """``(record, why_not)`` in the step recorder's format."""
        import _step_recorder as _sr
        files = set()
        for mod in list(sys.modules.values()):
            q = self._under_root(getattr(mod, "__file__", None))
            if q is not None:
                files.add(q)
        for tok in sorted(self._launched):
            for cand in (Path(tok), self._root / tok,
                         self._root / Path(tok).name):
                q = self._under_root(cand)
                if q is not None:
                    files.add(q)
                    files.update(p.resolve() for p in
                                 _sr._static_imports(q, self._root))
                    break
        if not files:
            return None, ("no plugin module was loaded by phase 1, so what it "
                          "ran cannot be established")
        out: Dict[str, Dict[str, str]] = {}
        for q in sorted(files):
            h = _sha256(q)
            if h is None:
                return None, f"{q} ran but could not be read"
            out[f"launched:{q.relative_to(self._root).as_posix()}"] = {
                "__whole__": h}
        out["__engine_env__"] = {name: _sr._engine_marker(name)
                                 for name in _sr.ENGINE_ENV}
        return out, ""


def sidecar_dir(project: Path) -> Path:
    return Path(project) / "phase1"


def _sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def generated_docs(project: Path) -> List[Path]:
    gd = _pl.generated_docs_dir(Path(project))
    return sorted(gd.glob("L*.json")) if gd.is_dir() else []


def docs_snapshot(project: Optional[Path]) -> Dict[str, Optional[str]]:
    """{L doc name: sha256} — what is on disk now ({} for no project)."""
    if project is None:
        return {}
    return {p.name: _sha256(p) for p in generated_docs(Path(project))}


def _input_roots(project: Path) -> List[Path]:
    roots = [Path(project) / "input"]
    if hasattr(_pl, "input_doc_dir"):
        roots.append(_pl.input_doc_dir(Path(project)))
    return roots


def inputs_digest(project: Path) -> Optional[str]:
    """sha256 over (relative path, sha256) of every design-input file."""
    pairs = []
    project = Path(project)
    for root in _input_roots(project):
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            if not f.is_file() or f.name.startswith("."):
                continue
            rel = f.relative_to(project)
            if any(p.lower() in _FORBIDDEN_PARTS for p in rel.parts):
                continue
            h = _sha256(f)
            if h is None:
                return None       # an unreadable input is not a known input
            pairs.append(f"{rel.as_posix()}\0{h}")
    return hashlib.sha256("\n".join(pairs).encode()).hexdigest()


def _plugin_version() -> str:
    try:
        root = Path(__file__).resolve().parent.parent
        doc = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
        return str(doc.get("version") or "")
    except (OSError, ValueError):
        return ""


def stamp(project: Path, recorder: Any) -> Dict[str, Any]:
    """Record THIS producer's identity beside the docs it just wrote.

    Called as phase 1's last act. Best-effort (never fails phase 1); a stamp
    that cannot be established reads as unknown, which regenerates next time
    -- more work, never a stale reuse."""
    project = Path(project)
    recording, why_not = (recorder.recorded() if recorder is not None
                          else (None, "phase 1 ran without a recorder"))
    code, code_why = (_si.code_from_recording(recording) if recording
                      else (None, [why_not]))
    ident = {"code": code, "inputs": inputs_digest(project)}
    outputs = {p.name: _sha256(p) for p in generated_docs(project)}
    extra = {"recording": recording, "outputs": outputs,
             "plugin_version": _plugin_version()}
    _si.write_sidecar(sidecar_dir(project), KIND, ident,
                      {"code": list(code_why)}, extra)
    return {"ident": ident, "outputs": outputs}


def restamp_outputs(project: Path) -> bool:
    """A phase-1 pass that is NOT the doc producer (the expert second track)
    rewrote docs: record their new bytes, keep the producer's recording.
    With no stamp to refresh, nothing is claimed -- the next run regenerates."""
    project = Path(project)
    rec = _si.read_sidecar(sidecar_dir(project), KIND)
    if rec is None:
        return False
    ident = {"code": rec.get("code"), "inputs": inputs_digest(project)}
    extra = {k: v for k, v in rec.items() if k not in ("code", "inputs",
                                                         "evidence")}
    extra["outputs"] = {p.name: _sha256(p) for p in generated_docs(project)}
    _si.write_sidecar(sidecar_dir(project), KIND, ident,
                      rec.get("evidence"), extra)
    return True


def _verdict(state: str, reason: str, why: str, **kw: Any) -> Dict[str, Any]:
    out = {"state": state, "reason": reason, "why": why}
    out.update(kw)
    return out


def assess(project: Path, programs_dir: Path) -> Dict[str, Any]:
    """REUSE / REGENERATE / REFUSE for the generated L docs on disk."""
    project = Path(project)
    gd = _pl.generated_docs_dir(project)
    rec = _si.read_sidecar(sidecar_dir(project), KIND)
    if rec is None or not isinstance(rec.get("outputs"), dict):
        return _verdict(
            REGENERATE, NO_PRODUCER_IDENTITY,
            "the generated L docs carry no producer identity "
            f"({sidecar_dir(project).name}/{_si.SIDECAR} kind {KIND!r}), so "
            "nothing proves they are what the current producer would write")
    outputs: Dict[str, Any] = rec["outputs"]
    on_disk = {p.name: p for p in generated_docs(project)}
    edited = sorted(n for n, h in outputs.items()
                    if n in on_disk and _sha256(on_disk[n]) != h)
    foreign = sorted(n for n in on_disk if n not in outputs)
    if edited or foreign:
        named = ", ".join(edited + foreign)
        return _verdict(
            REFUSE, GENERATED_DOC_EDITED,
            f"generated doc(s) {named} under {gd} no longer hold the bytes "
            f"phase 1 recorded ({'edited' if edited else ''}"
            f"{' and ' if edited and foreign else ''}"
            f"{'not written by phase 1' if foreign else ''}). They are neither "
            f"overwritten nor reused as current. Two ways out: (1) move the "
            f"edit into the design input under input/ and delete the doc, so "
            f"phase 1 regenerates it from the input; or (2) delete the doc to "
            f"discard the edit and regenerate it",
            docs=edited + foreign)
    removed = sorted(n for n in outputs if n not in on_disk)
    if removed:
        return _verdict(
            REGENERATE, GENERATED_DOC_REMOVED,
            f"generated doc(s) {', '.join(removed)} recorded by phase 1 are "
            f"gone; phase 1 regenerates them", docs=removed)
    old_code = rec.get("code")
    new_code, code_why = _si.code_from_stored(rec.get("recording"),
                                              Path(programs_dir))
    if not old_code or new_code is None or new_code != old_code:
        return _verdict(
            REGENERATE, PRODUCER_CHANGED,
            f"the phase-1 producer that wrote these docs is not the current "
            f"one: code {str(old_code or 'unrecorded')[:12]} -> "
            f"{str(new_code or 'unestablished')[:12]}, plugin "
            f"{rec.get('plugin_version') or '?'} -> "
            f"{_plugin_version() or '?'} ({'; '.join(code_why)})",
            old={"code": old_code, "plugin_version": rec.get("plugin_version")},
            new={"code": new_code, "plugin_version": _plugin_version()})
    new_inputs = inputs_digest(project)
    if not rec.get("inputs") or new_inputs != rec.get("inputs"):
        return _verdict(
            REGENERATE, DESIGN_INPUT_CHANGED,
            f"the design input changed since phase 1 wrote these docs: inputs "
            f"{str(rec.get('inputs') or 'unrecorded')[:12]} -> "
            f"{str(new_inputs or 'unreadable')[:12]}",
            old={"inputs": rec.get("inputs")}, new={"inputs": new_inputs})
    return _verdict(REUSE, "CURRENT",
                    f"every generated doc is byte-identical to what phase 1 "
                    f"recorded, the producer code re-derives to "
                    f"{str(new_code)[:12]} and the design input is unchanged")
