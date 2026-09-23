#!/usr/bin/env python3
"""_step_identity.py — PER-STEP freshness identity (owner ruling R-0924-3).

THE MEASURED DEFECT THIS REPLACES
---------------------------------
`phase3_one_shot_runner._producer_cache_valid_for` decided whether a cached
artefact was fresh from the BUILD that wrote it: `_plugin_version()` plus
`_recipe_sha256()`, where the recipe hash is the sha256 of the WHOLE
`phase3_one_shot_runner.py` — one ~66k-line file that authors the synth, PnR
and GDS recipes together. MEASURED on spm run23 (lane icspm5, 2026-09-23):

    [synth] the cached synth artefact was produced by a DIFFERENT build
    (plugin 1.23.70 -> 1.23.93; recipe e8fb29723876 -> cf8b2cd4ba76)
    — re-running

`--force-step gds` on a FINISHED tree therefore re-ran synthesis and started
PnR. Every landed fix bumps the version, so every landed fix invalidated every
cached step in the flow, whatever it actually touched.

ONE ROOT CAUSE, TWO OPPOSITE SYMPTOMS — and the old code already disclosed the
second one in a comment rather than fixing it:

  TOO COARSE  a version bump invalidates steps the change cannot reach.
  TOO NARROW  an in-tree edit to a HELPER module (`synth_frontend.py`,
              `_ppa/area.py`, …) with no version bump is NOT DETECTED, because
              the helper is not the runner file and the version did not move.

Both follow from identifying the BUILD instead of THE STEP'S OWN CODE. A fix
for only the coarse half would leave a real miss behind, so this module closes
both with one key.

WHAT A STEP'S IDENTITY IS
-------------------------
Four components, each answering "could this step's answer have changed?":

  inputs  sha256 of the files the step's DECLARED `required_inputs` resolve to
          in THIS run (flow yaml, `{from: <step>, path: <glob>}`, " OR "
          alternatives resolved first-hit exactly as `step_required_inputs_
          check` resolves them — one flow, one reading of its own contract).
  code    the step's OWN code, DERIVED, never listed by hand:
            * the module-level closure inside the runner reachable from the
              step's entry function (`step_synth` / `step_pnr` / `step_gds`) —
              functions AND the module-level constants they name, so a tuning
              constant is part of the recipe it tunes;
            * the step's declared `programs:` and the in-tree modules they and
              the runner closure DIRECTLY import — the half the old key missed.
              Depth 1 is measured, not assumed: see `direct_modules`.
          A hand-maintained list is exactly how `canonical_run_admission.
          canonical_program_paths()` came to name three files and miss
          `_ppa/area.py`. Derived or it rots.
  tools   the tool versions this step actually invoked, read back from
          `provenance.jsonl` (which already records `tool`, `version` and
          `outputs: {path -> sha256:<hex>}` per invocation), plus the container
          image digest from `_eda_pin.container_image_digest`.
  pdk     sha256 of the PDK files the step reads (liberty, tech LEF, cell LEF,
          cell GDS, DRC deck) — resolved from `PdkConfig`, host-side only.

FAILS CLOSED, EVERYWHERE, AND THAT IS NOT NEGOTIABLE
----------------------------------------------------
Every component returns `None` when it cannot be computed, and `None` never
compares equal — not even to another `None`. An unresolvable glob, an
unreadable source file, a missing provenance record, a PDK path that is
container-only: each makes the step STALE and it re-runs. Reuse requires
POSITIVE PROOF that nothing moved; the absence of evidence is never freshness.
That is the doctrine the old predicate already held, and it survives intact.

CHIP-AGNOSTIC: no design, PDK, vendor or foundry token appears here. The PDK
is hashed as opaque bytes at paths the caller resolved.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# The ONE anchor in this module: which function in the runner IS the step.
# Everything else about `code` is derived from these by walking the module.
# A rename that is not reflected here makes the closure empty, and an empty
# closure is refused (`code` becomes None -> re-run), never silently accepted:
# `test_r0924_3_*` asserts each seed resolves in the real runner.
KIND_RECIPE_SEEDS: Dict[str, Tuple[str, ...]] = {
    "synth": ("step_synth",),
    "pnr": ("step_pnr",),
    "gds": ("step_gds",),
}

# Which DECLARED flow output identifies the step(s) that produce this kind.
# Matched against `required_outputs` so the step ids themselves are derived
# from the flow rather than written down here (they move; the artefact does
# not). Suffix match, so a declared "phase3/stage4/gds/*.gds" is found.
KIND_OUTPUT_MARK: Dict[str, str] = {
    "synth": "synth/netlist.v",
    "pnr": "pnr/routed.def",
    "gds": ".gds",
}

# Where each kind's artefacts live, for selecting its provenance entries.
KIND_DIR_PREFIX: Dict[str, Tuple[str, ...]] = {
    "synth": ("phase2/stage2/synth/",),
    "pnr": ("phase3/stage3/pnr/",),
    "gds": ("phase3/stage4/gds/", "phase3/stage3/gds/"),
}

SIDECAR = "step_identity.json"
_COMPONENTS = ("inputs", "code", "tools", "pdk")


# --------------------------------------------------------------------------
# hashing helpers
# --------------------------------------------------------------------------
def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(p: Path) -> Optional[str]:
    """sha256 of a file, or None when it cannot be read.

    None is the honest answer and it propagates: a component that could not
    read one of its own members has no digest, and no digest means re-run."""
    try:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _digest_pairs(pairs: Sequence[Tuple[str, str]]) -> str:
    """One digest over (name, member-digest) pairs, order-independent."""
    h = hashlib.sha256()
    for name, dig in sorted(pairs):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(dig.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


# --------------------------------------------------------------------------
# flow reading — one flow, read the way the flow's own checker reads it
# --------------------------------------------------------------------------
def load_steps(flow_yaml: Path) -> List[Dict[str, Any]]:
    """The flow's steps, or [] when the flow cannot be read (-> fail closed)."""
    try:
        import yaml  # noqa: PLC0415 — optional at import time, required here
        doc = yaml.safe_load(Path(flow_yaml).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — unreadable flow must not raise here
        return []
    steps = (doc or {}).get("steps") or []
    return steps if isinstance(steps, list) else []


def steps_for_kind(steps: Sequence[Dict[str, Any]], kind: str
                   ) -> List[Dict[str, Any]]:
    """The declared producer step(s) of `kind`, found by their OUTPUT.

    Derived from the flow rather than hard-coded by id, because ids move and
    the artefact a step owes does not. Analog and mixed-signal GDS outputs are
    excluded: they are a different deliverable with their own steps."""
    mark = KIND_OUTPUT_MARK.get(kind)
    if not mark:
        return []

    def _consumes(step: Dict[str, Any]) -> bool:
        """True when the step declares this artefact as an INPUT.

        MEASURED: step 14 ("Synthesis handoff gate") re-declares
        `phase2/stage2/synth/netlist.v` in its `required_outputs` while also
        declaring it as a `required_input` from step 9. It is a GATE over the
        netlist, not its producer, and treating it as one dragged
        `flow_compliance_check`'s ~460-file import closure into synth's
        identity — which would have made a die-finishing edit invalidate the
        cached netlist, the very thing R-0924-3 exists to stop. A step that
        reads the artefact did not write it."""
        for e in (step.get("required_inputs") or []):
            if not isinstance(e, dict):
                continue
            spec = e.get("path")
            if not isinstance(spec, str):
                continue
            for alt in (x.strip() for x in spec.split(" OR ")):
                if alt.endswith(mark) or mark in alt:
                    return True
        return False

    out: List[Dict[str, Any]] = []
    for s in steps:
        if _consumes(s):
            continue
        for o in (s.get("required_outputs") or []):
            if not isinstance(o, str):
                continue
            for alt in (x.strip() for x in o.split(" OR ")):
                if not alt.endswith(mark) and mark not in alt:
                    continue
                if any(t in alt for t in ("analog", "hardmacro",
                                          "mixed_signal", "foundry_handoff")):
                    continue
                out.append(s)
                break
            else:
                continue
            break
    return out


def _glob_first(project: Path, spec: str) -> List[Path]:
    """First non-empty match for one declared path spec, sorted.

    `spec` may be a literal or a glob; the caller splits " OR " before this."""
    try:
        if any(ch in spec for ch in "*?["):
            return sorted(p for p in project.glob(spec) if p.is_file())
        p = project / spec
        return [p] if p.is_file() else []
    except (OSError, ValueError):
        return []


# --------------------------------------------------------------------------
# component: inputs
# --------------------------------------------------------------------------
def inputs_digest(project: Path, steps: Sequence[Dict[str, Any]], kind: str
                  ) -> Tuple[Optional[str], List[str]]:
    """sha256 over the step's DECLARED inputs as they exist in this run.

    Returns `(digest, evidence)`. `digest` is None — meaning re-run — when the
    kind has no declared producer step, when a declared input resolves to
    nothing, or when a resolved file cannot be read. A declared input that is
    not there is not "no input": it is an unanswerable question."""
    producers = steps_for_kind(steps, kind)
    if not producers:
        return None, [f"no flow step declares an output matching "
                      f"{KIND_OUTPUT_MARK.get(kind, '?')!r}, so this kind has "
                      f"no declared inputs to hash"]
    pairs: List[Tuple[str, str]] = []
    evidence: List[str] = []
    for s in producers:
        entries = s.get("required_inputs") or []
        for e in entries:
            if not isinstance(e, dict):
                continue
            spec = e.get("path")
            if not isinstance(spec, str) or not spec:
                # A declared external input with no project-relative path is
                # recorded by the flow as unprobeable; it cannot be hashed and
                # it is not pretended to be satisfied.
                continue
            hits: List[Path] = []
            for alt in (x.strip() for x in spec.split(" OR ")):
                hits = _glob_first(Path(project), alt)
                if hits:
                    break
            if not hits:
                return None, [f"declared input {spec!r} (from step "
                              f"{e.get('from')}) resolves to no file in this "
                              f"run, so this step's inputs cannot be hashed"]
            for h in hits:
                d = _sha256_file(h)
                if d is None:
                    return None, [f"declared input {h} could not be read"]
                rel = os.path.relpath(h, Path(project))
                pairs.append((rel, d))
                evidence.append(f"{rel}={d[:12]}")
    if not pairs:
        return None, ["the declared producer step(s) name no hashable input"]
    return _digest_pairs(pairs), evidence


# --------------------------------------------------------------------------
# component: code — DERIVED, never a hand-kept list
# --------------------------------------------------------------------------
def _module_members(path: Path) -> Tuple[Dict[str, str], Optional[str]]:
    """Module-level functions AND assignments, name -> exact source segment.

    Constants are members because a recipe's tuning constant is part of that
    recipe: changing `_PLACEMENT_DENSITY_FLOOR` changes what the placer is
    asked for just as surely as editing the call that reads it."""
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"{path.name} unreadable: {exc}"
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return {}, f"{path.name} unparseable: {exc}"
    # Slice by line span against a ONE-TIME split. `ast.get_source_segment`
    # re-splits the whole file per node, which is quadratic and measurably so:
    # the runner is ~66k lines and the naive form did not finish in 120 s.
    lines = src.splitlines(keepends=True)

    def _seg(node: ast.AST) -> Optional[str]:
        lo = getattr(node, "lineno", None)
        hi = getattr(node, "end_lineno", None)
        if lo is None or hi is None:
            return None
        # Include the decorators: a changed decorator changes the function.
        for dec in getattr(node, "decorator_list", ()) or ():
            dlo = getattr(dec, "lineno", None)
            if dlo is not None and dlo < lo:
                lo = dlo
        return "".join(lines[lo - 1:hi])

    out: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            seg = _seg(node)
            if seg is not None:
                out[node.name] = seg
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            seg = _seg(node)
            if seg is None:
                continue
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            for t in targets:
                if isinstance(t, ast.Name):
                    out[t.id] = seg
    return out, None


def _referenced_names(segment: str) -> Set[str]:
    """Every bare name a source segment mentions.

    Deliberately an OVER-approximation: any `Name` reference counts, not only
    a call. Over-approximating pulls in more members than strictly reachable,
    which can only cause MORE invalidation — the safe direction. Under-
    approximating would let an edit to a genuinely used helper go unseen,
    which is the second half of the defect this module exists to close."""
    try:
        tree = ast.parse(segment)
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def runner_code_closure(runner_path: Path, kind: str
                        ) -> Tuple[Optional[str], List[str]]:
    """Digest of the runner members reachable from this kind's entry point.

    This is the move that fixes the coarse half. The old key hashed the whole
    66k-line file, so an edit to the GDS path invalidated the cached netlist.
    Here the closure starts at `step_synth` / `step_pnr` / `step_gds` and walks
    module-level references, so an edit reaches only the kinds that can see
    it — and an edit anywhere those kinds DO reach still invalidates them."""
    seeds = KIND_RECIPE_SEEDS.get(kind)
    if not seeds:
        return None, [f"no recipe seed declared for kind {kind!r}"]
    members, err = _module_members(Path(runner_path))
    if err:
        return None, [err]
    missing = [s for s in seeds if s not in members]
    if missing:
        return None, [f"recipe seed(s) {', '.join(missing)} are not defined in "
                      f"{Path(runner_path).name} — the anchor moved and the "
                      f"closure cannot be computed, so nothing is reused"]
    seen: Set[str] = set()
    stack: List[str] = list(seeds)
    while stack:
        name = stack.pop()
        if name in seen or name not in members:
            continue
        seen.add(name)
        for ref in _referenced_names(members[name]):
            if ref in members and ref not in seen:
                stack.append(ref)
    pairs = [(n, _sha256_bytes(members[n].encode("utf-8"))) for n in seen]

    # THE SECOND HALF OF THE DEFECT, and measuring is what caught it.
    # `step_pnr` reaches `_ppa/area.py` through `import _ppa.area as
    # _ppa_area` in the runner, NOT through any step's declared `programs:`.
    # Hashing only the runner's own member text would therefore have left an
    # edit to `real_core_placement_density` invisible — the same blind spot
    # `canonical_program_paths()` has. So the names a closure member REFERENCES
    # are resolved against the runner's import bindings, and anything that
    # lands inside `programs/` is digested with its own transitive closure.
    bindings = _import_bindings(Path(runner_path))
    reached: Set[str] = set()
    for name in seen:
        for ref in _referenced_names(members[name]):
            if ref in bindings:
                reached.add(bindings[ref])
    mods: List[Path] = []
    programs_dir = Path(runner_path).resolve().parent
    for dotted in sorted(reached):
        m = _resolve_module(programs_dir, dotted)
        if m is not None:
            mods.append(m)
    if mods:
        files, _notes = direct_modules(mods, programs_dir)
        for f in files:
            d = _sha256_file(f)
            if d is None:
                return None, [f"runner-imported module {f.name} unreadable"]
            try:
                rel = str(f.resolve().relative_to(programs_dir))
            except ValueError:
                rel = f.name
            pairs.append((f"import:{rel}", d))
    return _digest_pairs(pairs), [
        f"{len(seen)} runner member(s) reachable from {', '.join(seeds)}, "
        f"plus {len(pairs) - len(seen)} in-tree module(s) they directly import"]


def _import_bindings(path: Path) -> Dict[str, str]:
    """`bound name -> dotted module`, for every import ANYWHERE in a module.

    Function-local imports count: the runner imports `step_force`, `_ppa.area`
    and others inside the functions that use them, which is precisely where a
    step's real dependencies live."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}
    out: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module and \
                not node.level:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
                out.setdefault(node.module.split(".")[0], node.module)
    return out


def _resolve_module(programs_dir: Path, dotted: str) -> Optional[Path]:
    """`_ppa.area` -> programs/_ppa/area.py, when it is ours. None if not."""
    parts = dotted.split(".")
    base = Path(programs_dir).joinpath(*parts)
    for cand in (base.with_suffix(".py"), base / "__init__.py"):
        try:
            if cand.is_file() and cand.resolve().is_relative_to(
                    Path(programs_dir).resolve()):
                return cand
        except (OSError, ValueError):
            continue
    return None


def direct_modules(entries: Iterable[Path], programs_dir: Path
                   ) -> Tuple[List[Path], List[str]]:
    """The entry files plus the in-tree modules they DIRECTLY import.

    THE HALF THE OLD KEY MISSED, and the depth is a MEASURED choice, not a
    guess. `canonical_run_admission.canonical_program_paths()` names three
    files by hand and so cannot see a change in a helper; walking imports is
    the only version that does not rot. But walking them TRANSITIVELY
    degenerates in this tree — measured on main b23f6d190:

        _watchdog.py -> step_input_scope.py -> step_required_inputs_check.py
        -> flow_compliance_check.py -> phase1_doc_one_shot_runner.py

    and that last module imports 123 more. Every kind's transitive closure is
    the same 459 files, so under it a die-finishing edit would invalidate the
    cached NETLIST — precisely the harm R-0924-3 exists to stop. At depth 1 the
    sets separate and are right: `die_finishing_gen.py` is in pnr's and gds's
    and NOT in synth's.

    DISCLOSED RESIDUAL, in the same spirit as the predicate this replaces
    disclosed its own: a change to a SECOND-ORDER helper — imported by a direct
    module but never named by the step itself — is not detected here. It is a
    smaller hole than the one being closed, it is stated rather than hidden,
    and branch (2) is where the per-step graph can close it properly."""
    programs_dir = Path(programs_dir)
    out: List[Path] = []
    notes: List[str] = []
    seen: Set[Path] = set()

    def _add(p: Path) -> bool:
        try:
            rp = p.resolve()
        except OSError:
            return False
        if rp in seen or not p.is_file():
            return False
        seen.add(rp)
        out.append(p)
        return True

    for entry in entries:
        cur = Path(entry)
        if not _add(cur):
            continue
        try:
            tree = ast.parse(cur.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            notes.append(f"{cur.name}: {type(exc).__name__}")
            continue
        for node in ast.walk(tree):
            names: List[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and \
                    not node.level:
                names = [node.module] + [f"{node.module}.{a.name}"
                                         for a in node.names]
            for dotted in names:
                m = _resolve_module(programs_dir, dotted)
                if m is not None:
                    _add(m)
    return out, notes


def program_code_digest(programs_dir: Path, steps: Sequence[Dict[str, Any]],
                        kind: str) -> Tuple[Optional[str], List[str]]:
    """Digest of the step's declared `programs:` plus their import closure."""
    producers = steps_for_kind(steps, kind)
    if not producers:
        return None, ["no declared producer step, so no declared programs"]
    entries: List[Path] = []
    for s in producers:
        for name in (s.get("programs") or []):
            p = Path(programs_dir) / f"{name}.py"
            if p.is_file():
                entries.append(p)
    if not entries:
        # A step that declares no program of its own is not an error; the
        # runner members carry its recipe. An EMPTY set is reported as such,
        # never as a match.
        return _digest_pairs(()), ["step declares no program file of its own"]
    files, notes = direct_modules(entries, programs_dir)
    pairs: List[Tuple[str, str]] = []
    for f in files:
        d = _sha256_file(f)
        if d is None:
            return None, [f"program {f.name} could not be read"]
        try:
            rel = str(f.resolve().relative_to(Path(programs_dir).resolve()))
        except ValueError:
            rel = f.name
        pairs.append((rel, d))
    return _digest_pairs(pairs), [f"{len(pairs)} program file(s): the step's own "
                                  f"and their direct in-tree imports"] + notes


def code_digest(runner_path: Path, programs_dir: Path,
                steps: Sequence[Dict[str, Any]], kind: str
                ) -> Tuple[Optional[str], List[str]]:
    """The step's OWN code: runner closure + declared programs' closure."""
    r, r_why = runner_code_closure(runner_path, kind)
    p, p_why = program_code_digest(programs_dir, steps, kind)
    if r is None or p is None:
        return None, r_why + p_why
    return _digest_pairs((("runner", r), ("programs", p))), r_why + p_why


# --------------------------------------------------------------------------
# component: tools
# --------------------------------------------------------------------------
def tools_digest(project: Path, kind: str, image_digest: Optional[str]
                 ) -> Tuple[Optional[str], List[str]]:
    """Tool versions this step actually invoked, plus the image digest.

    Read back from `provenance.jsonl`, which already records `tool`, `version`
    and `outputs: {path -> sha256:<hex>}` for every invocation — so "which
    tools did THIS step run" is answered by the run's own ledger rather than
    by a list somebody keeps in sync."""
    prefixes = KIND_DIR_PREFIX.get(kind) or ()
    prov = Path(project) / "provenance.jsonl"
    if not prov.is_file():
        return None, ["no provenance.jsonl, so the tools this step ran cannot "
                      "be established"]
    versions: Dict[str, str] = {}
    try:
        for line in prov.read_text(encoding="utf-8",
                                   errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            outs = rec.get("outputs") or {}
            if not isinstance(outs, dict):
                continue
            if not any(str(k).startswith(pfx)
                       for k in outs for pfx in prefixes):
                continue
            tool = str(rec.get("tool") or "")
            if tool:
                versions[tool] = str(rec.get("version") or "")
    except OSError as exc:
        return None, [f"provenance.jsonl unreadable: {exc}"]
    if not versions:
        return None, [f"provenance.jsonl records no invocation writing into "
                      f"{', '.join(prefixes) or '?'}, so this step's tools are "
                      f"unknown"]
    pairs = [(t, _sha256_bytes(v.encode("utf-8")))
             for t, v in versions.items()]
    if image_digest:
        pairs.append(("__image__", _sha256_bytes(image_digest.encode())))
    else:
        return None, ["the container image digest is unavailable, so the tool "
                      "identity cannot be established"]
    return _digest_pairs(pairs), [f"{len(versions)} tool(s): "
                                  f"{', '.join(sorted(versions))}"]


# --------------------------------------------------------------------------
# component: pdk
# --------------------------------------------------------------------------
_PDK_FIELDS = ("liberty", "tech_lef", "cell_lef", "cell_gds", "drc_deck")


def pdk_files(pdk: Any) -> List[Tuple[str, str]]:
    """`(field, path)` for every PDK file this flow declares, in order."""
    out: List[Tuple[str, str]] = []
    for field in _PDK_FIELDS:
        val = getattr(pdk, field, None)
        if val:
            out.append((field, str(val)))
    return out


def pdk_digest(pdk: Any, hasher: Any = None
               ) -> Tuple[Optional[str], List[str]]:
    """sha256 of the PDK files this step reads.

    WHERE THE FILES ACTUALLY ARE — a MEASURED correction to my own first cut.
    `PdkConfig.liberty` and its siblings are documented as "path inside
    container (or host, if absolute exists)", and on the measured
    configuration they are container-ONLY: run2's liberty is
    `/foss/pdks/ciel/.../gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib` and THE
    HOST HAS NO `/foss/pdks` AT ALL. A host-side `is_file()` therefore answers
    False for every PDK file, this component is never computable, and the
    whole change degenerates into "always re-run" — strictly WORSE than the
    predicate it replaces. The existing deck's control assertion
    (`test_matching_producer_is_reusable`: "the fix must not degenerate into
    'always re-run'") is what caught it, which is the argument for never
    deleting a test one's own change has inconvenienced.

    So the caller supplies a `hasher` that reads the files where they live.
    With no hasher only host-resolvable paths are used, and an unresolvable
    one still refuses: the fail-closed rule is unchanged, it is simply no
    longer tripped by the ordinary case."""
    if pdk is None:
        return None, ["no PDK configuration in hand"]
    want = pdk_files(pdk)
    if not want:
        return None, ["the PDK configuration names no file"]
    digests: Dict[str, str] = {}
    if hasher is not None:
        try:
            got = hasher([path for _f, path in want])
        except Exception as exc:  # noqa: BLE001 — a failed probe is not a pass
            return None, [f"the PDK hasher failed ({type(exc).__name__}: "
                          f"{exc}), so the PDK cannot be proven unchanged"]
        if isinstance(got, dict):
            digests.update({str(k): str(v) for k, v in got.items() if v})
    pairs: List[Tuple[str, str]] = []
    unresolved: List[str] = []
    for field, path in want:
        d = digests.get(path)
        if d is None:
            p_ = Path(path)
            d = _sha256_file(p_) if p_.is_file() else None
        if d is None:
            unresolved.append(f"{field}={path}")
            continue
        pairs.append((field, d))
    if unresolved:
        return None, [f"PDK file(s) could not be read, so the PDK cannot be "
                      f"proven unchanged: {'; '.join(unresolved)}"]
    return _digest_pairs(pairs), [f"{len(pairs)} PDK file(s)"]


# --------------------------------------------------------------------------
# the identity, and the comparison
# --------------------------------------------------------------------------
def identity_now(*, project: Path, kind: str, runner_path: Path,
                 programs_dir: Path, flow_yaml: Path, pdk: Any = None,
                 image_digest: Optional[str] = None, pdk_hasher: Any = None
                 ) -> Tuple[Dict[str, Optional[str]], Dict[str, List[str]]]:
    """This build's identity for `kind`, plus per-component evidence."""
    steps = load_steps(Path(flow_yaml))
    why: Dict[str, List[str]] = {}
    ident: Dict[str, Optional[str]] = {}
    ident["inputs"], why["inputs"] = inputs_digest(Path(project), steps, kind)
    ident["code"], why["code"] = code_digest(
        Path(runner_path), Path(programs_dir), steps, kind)
    ident["tools"], why["tools"] = tools_digest(
        Path(project), kind, image_digest)
    ident["pdk"], why["pdk"] = pdk_digest(pdk, pdk_hasher)
    return ident, why


def compare(recorded: Optional[Dict[str, Any]],
            now: Dict[str, Optional[str]]) -> Tuple[bool, List[str]]:
    """`(fresh, reasons)`. Fresh ONLY on positive proof of every component.

    An unknown (None) component on EITHER side is a mismatch, and two unknowns
    do not agree: "I could not tell then" and "I cannot tell now" is not
    evidence that nothing moved."""
    if not isinstance(recorded, dict):
        return False, ["no recorded step identity — the artefact was written "
                       "by an unknown step state"]
    reasons: List[str] = []
    fresh = True
    for comp in _COMPONENTS:
        old = recorded.get(comp)
        new = now.get(comp)
        if new is None:
            fresh = False
            reasons.append(f"{comp}: THIS run's value cannot be established")
        elif not isinstance(old, str) or not old:
            fresh = False
            reasons.append(f"{comp}: the cached artefact carries no value")
        elif old != new:
            fresh = False
            reasons.append(f"{comp}: {old[:12]} -> {new[:12]}")
        else:
            reasons.append(f"{comp} unchanged ({new[:12]})")
    return fresh, reasons


def read_sidecar(out_dir: Path, kind: str) -> Optional[Dict[str, Any]]:
    try:
        doc = json.loads((Path(out_dir) / SIDECAR).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rec = doc.get(kind) if isinstance(doc, dict) else None
    return rec if isinstance(rec, dict) else None


def write_sidecar(out_dir: Path, kind: str, ident: Dict[str, Optional[str]],
                  why: Optional[Dict[str, List[str]]] = None) -> None:
    """Stamp `kind`'s identity. Best-effort, exactly like its predecessor: a
    stamp failure must never fail a step that succeeded, and an absent stamp
    reads as 'unknown' -> re-run, which can only cause more work."""
    try:
        p = Path(out_dir) / SIDECAR
        doc: Dict[str, Any] = {}
        if p.is_file():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    doc = loaded
            except (OSError, ValueError):
                doc = {}
        rec: Dict[str, Any] = dict(ident)
        if why:
            rec["evidence"] = {k: list(v) for k, v in why.items()}
        doc[kind] = rec
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    except Exception:  # nosec — sidecar is best-effort, like its predecessor
        pass
