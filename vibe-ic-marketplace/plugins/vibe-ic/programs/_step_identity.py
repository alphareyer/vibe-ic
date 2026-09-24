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
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# The ONE anchor in this module: which function in the runner IS the step.
# Everything else about `code` is derived from these by walking the module.
# A rename that is not reflected here makes the closure empty, and an empty
# closure is refused (`code` becomes None -> re-run), never silently accepted:
# `test_r0924_3_*` asserts each seed resolves in the real runner.
KIND_RECIPE_SEEDS: Dict[str, Tuple[str, ...]] = {
    "synth": ("step_synth",),
    # r2 review finding B: `step_pnr` is not the only writer of the artefact
    # this key protects. `step_signoff_spef_repair` and
    # `step_signoff_drv_wire_length_repair` run AFTER the PnR stamp and
    # `shutil.copy2` a repaired DEF straight over `routed.def` and
    # `{top}.def`. A fix landed in either of them changed the cached DEF and
    # changed nothing in the key that vouched for it. A step's code is every
    # function that writes its artefact, not the one that is named after it.
    "pnr": ("step_pnr", "step_signoff_spef_repair",
            "step_signoff_drv_wire_length_repair"),
    "gds": ("step_gds", "step_signoff_spef_repair",
            "step_signoff_drv_wire_length_repair",
            "step_canonicalize_artefacts"),
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

# THE SPAN each runner function implements, as the flow's own endpoints.
#
# REVIEW FINDING (R-0924-3 r1, wcxu446tu — 6 CONFIRMED, root cause here): the
# first cut tied each kind to the ONE step that declares its artefact, and that
# is not what the code does. `step_pnr` is floorplan THROUGH routing; `step_gds`
# plus the in-place finishing around it spans die finishing through stream-out.
# Keying pnr on step 21 alone made its inputs `sha(post_hold.def)` — a file
# `step_pnr` WRITES ITSELF — so a new netlist, a new SDC or a new slot left the
# routed DEF "fresh" and the disclosure said `inputs unchanged`. A self-
# referential cache key is worse than no key: it is a key that cannot fire.
#
# Endpoints only; MEMBERSHIP is derived from the flow's own ordering, so a step
# inserted into a span joins it without anything here being edited. A test
# asserts each endpoint resolves and that the interior members it must contain
# are present.
KIND_SPAN: Dict[str, Tuple[str, str]] = {
    "synth": ("9", "9"),
    "pnr": ("15", "21"),
    "gds": ("26.5ic", "37"),
}

# Where each kind's artefacts live, for selecting its provenance entries.
KIND_DIR_PREFIX: Dict[str, Tuple[str, ...]] = {
    "synth": ("phase2/stage2/synth/",),
    "pnr": ("phase3/stage3/pnr/",),
    "gds": ("phase3/stage4/gds/", "phase3/stage3/gds/"),
}

#: Marks the "resolve `from X import f` as module X" fallback binding.
_FROM_FALLBACK = "\0from:"

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
def steps_in_span(steps: Sequence[Dict[str, Any]], kind: str
                  ) -> List[Dict[str, Any]]:
    """Every flow step the runner function for `kind` implements.

    Derived from the flow's ORDER between the declared endpoints, so this does
    not have to be re-listed when a step is inserted."""
    span = KIND_SPAN.get(kind)
    if not span:
        return []
    ids = [str(s.get("id")) for s in steps]
    try:
        lo, hi = ids.index(span[0]), ids.index(span[1])
    except ValueError:
        return []
    if lo > hi:
        return []
    return list(steps[lo:hi + 1])


def _declared_paths(entries: Any) -> List[str]:
    out: List[str] = []
    for e in entries or ():
        spec = e.get("path") if isinstance(e, dict) else e
        if isinstance(spec, str) and spec:
            out.append(spec)
    return out


def span_input_specs(steps: Sequence[Dict[str, Any]], kind: str
                     ) -> Tuple[List[Tuple[str, bool]], List[str]]:
    """`([(spec, producer_is_conditional)], produced_inside)` for the span.

    THE UNION of every `required_inputs` path declared by any step in the span,
    MINUS everything the span PRODUCES itself. The subtraction is the point: a
    step's own output is not evidence about whether that step should re-run,
    and hashing it is what made the first cut unable to fire.

    THE CONDITIONAL FLAG, which measurement forced. `step_pnr`'s span declares
    `phase2/stage2/synth/post_dft_netlist.v`, owed by step 12 — and step 12 is
    `condition_kind: design_dependent`, owed only where the design declares
    DFT. A real finished gf180 tree (probeSPM_A3) does not have that file, so
    treating its absence as an unanswerable question would refuse for ever and
    PnR would NEVER be reused on any design without DFT, which is most of them.
    An input whose producer is conditional is therefore recorded as ABSENT
    rather than refused — and `absent` is a VALUE, so if the file ever appears
    the identity moves and the step re-runs. Nothing is lost; a change is still
    always detected. An UNCONDITIONAL declared input that is missing still
    refuses, because that is a broken tree, not a design choice."""
    members = steps_in_span(steps, kind)
    by_id = {str(s.get("id")): s for s in steps}
    produced: Set[str] = set()
    for s in members:
        for spec in _declared_paths(s.get("required_outputs")):
            for alt in (x.strip() for x in spec.split(" OR ")):
                produced.add(alt)
    specs: List[Tuple[str, bool]] = []
    seen: Set[str] = set()
    for s in members:
        for e in (s.get("required_inputs") or ()):
            spec = e.get("path") if isinstance(e, dict) else e
            if not isinstance(spec, str) or not spec:
                continue
            alts = [x.strip() for x in spec.split(" OR ")]
            if all(a in produced for a in alts):
                continue          # made inside the span: not an input to it
            if spec in seen:
                continue
            seen.add(spec)
            producer = by_id.get(str(e.get("from"))) if isinstance(e, dict) \
                else None
            conditional = bool(producer and producer.get("condition"))
            specs.append((spec, conditional))
    return specs, sorted(produced)


#: How a file's bytes are canonicalised before hashing, by a STATED rule.
#:
#: r2 review (wt0nrjhv6) findings C/E and D: two files a step genuinely READS
#: are rewritten by the flow itself, so hashing them raw makes the step either
#: never fresh or fresh on the wrong evidence.
#:   * a SPEF carries `*DATE "14:51:35 Wednesday September 23, 2026"` — a wall
#:     clock, so no two runs ever agree;
#:   * `tapeout_declaration.json` is REWRITTEN by `step_gds`
#:     (`publish_tapeout_declarations`) with the keys the flow DERIVED, after
#:     the synth and PnR stamps — so synth and PnR could never be fresh on the
#:     next run for a change they did not make.
#: The rule is named at the call site, applied here, and DISCLOSED in the
#: evidence, so nothing is quietly dropped.
NORMALISERS: Tuple[str, ...] = ("raw", "spef_no_date", "declaration_as_asked")

#: A `"something.py"` string literal — how a program run by file path
#: names itself in the source that dispatches it.
_PY_PATH_LITERAL_RE = re.compile(r"[\"']([A-Za-z0-9_./-]+\.py)[\"']")

_SPEF_DATE_RE = re.compile(rb"^\*DATE\b.*$", re.MULTILINE)


def canonical_bytes(path: Path, rule: str = "raw") -> Optional[bytes]:
    """The bytes of `path` that are actually EVIDENCE, under a named rule."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if rule == "raw":
        return raw
    if rule == "spef_no_date":
        # The only volatile line in the format, and it is a clock.
        return _SPEF_DATE_RE.sub(b'*DATE "<normalised>"', raw)
    if rule == "declaration_as_asked":
        # Everything the OPERATOR asked for, and nothing the flow wrote back.
        #
        # THE RULE IS NARROWER THAN "DROP THE PUBLISHED KEYS", and the first
        # cut of it was WRONG in a way that mattered: `deliverable` is in
        # `_DECLARATION_PUBLISH_KEYS`, so dropping every published key hid a
        # genuine OPERATOR change from DIE to HARDMACRO — the single answer
        # that changes the most about a run. `publish_tapeout_declarations`
        # writes "only the fields it could derive AND THAT NOBODY HAS ALREADY
        # ANSWERED", and the declaration records who answered what in
        # `answer_provenance`. So an answer is dropped only when it is BOTH a
        # key the flow may publish AND one no one has claimed. An
        # operator-answered key is kept, because the flow will never overwrite
        # it. `answer_provenance` itself is operator input and is hashed.
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return raw
        if not isinstance(doc, dict):
            return raw
        answers = doc.get("answers")
        doc = dict(doc)
        if isinstance(answers, dict):
            prov = doc.get("answer_provenance")
            claimed = set(prov) if isinstance(prov, dict) else set()
            doc["answers"] = {
                k: v for k, v in answers.items()
                if k in claimed or k not in _DECLARATION_FLOW_KEYS}
        # r3 review finding 4: `step_gds` does not only rewrite `answers`. Its
        # merge also writes the TOP-LEVEL `from_the_technology` and
        # `forbidden_layers` (the EXTRA_KEY path), so a phase-3-only re-run
        # moved synth's and pnr's input hash for something the FLOW wrote.
        # Dropped only when unclaimed, exactly as the answers are.
        for extra in _DECLARATION_FLOW_TOP_KEYS:
            if extra in doc and extra not in _DECLARATION_CLAIMED_TOP:
                doc.pop(extra, None)
        return json.dumps(doc, sort_keys=True).encode("utf-8")
    return raw


#: Set by the caller (the runner owns the list; this module must not restate
#: it). Empty means "no key is known to be flow-written", which is the honest
#: default and makes `declaration_as_asked` behave as `raw`.
_DECLARATION_FLOW_KEYS: Set[str] = set()

#: TOP-LEVEL keys `step_gds`'s declaration merge also rewrites (r3 finding 4).
_DECLARATION_FLOW_TOP_KEYS: Tuple[str, ...] = ("from_the_technology",
                                               "forbidden_layers")
#: …unless the operator claimed them, which `set_declaration_flow_keys`'s
#: caller records. Empty by default: nothing is assumed claimed.
_DECLARATION_CLAIMED_TOP: Set[str] = set()


def set_declaration_flow_keys(keys: Iterable[str],
                              claimed_top: Iterable[str] = ()) -> None:
    """Tell this module which declaration answers the FLOW writes back, and
    which TOP-LEVEL keys the operator has claimed (so they are kept)."""
    global _DECLARATION_FLOW_KEYS, _DECLARATION_CLAIMED_TOP
    _DECLARATION_FLOW_KEYS = set(keys)
    _DECLARATION_CLAIMED_TOP = set(claimed_top)


def resolved_inputs_digest(project: Path,
                           inputs: Sequence[Tuple[str, Any, str]],
                           knobs: Dict[str, str]
                           ) -> Tuple[Optional[str], List[str]]:
    """sha256 over what the step ACTUALLY READS, as the step resolved it.

    r2 review finding A, and it is the reason this replaces the flow-derived
    list rather than extending it. `step_pnr` does not read
    `phase2/stage2/constraints/<top>.sdc` — that is a COPY the runner writes
    once. It reads whatever `sdc_constraints.collect_sdc_files` resolves out of
    `input/constraints/` and `input/reference_flow/`, or a file it builds from
    L8/L9. It reads the slot and die rectangle out of
    `reports/phase1/submission_template.json`. And its behaviour turns on
    `--spare-density`, `VIBEIC_TAP_PITCH_UM` and `VIBEIC_FORCE_KLAYOUT_
    STREAMOUT`, none of which is a file at all. A cache key that hashes a
    stand-in the real path never opens is not a cache key.

    So the RUNNER resolves its own inputs, the same way the step does, and
    hands them here as `(label, path, rule)`. `knobs` are the non-file inputs.

    FAILS CLOSED, and harder than before: an EMPTY input set is a refusal, not
    an empty product. If the caller could not enumerate what the step reads,
    this cannot answer whether it changed."""
    if not inputs:
        return None, ["the caller enumerated no input for this step, so what "
                      "it reads cannot be established"]
    pairs: List[Tuple[str, str]] = []
    evidence: List[str] = []
    for label, path, rule in inputs:
        if path is None:
            return None, [f"{label}: the step's own resolution produced no "
                          f"path, so this input cannot be hashed"]
        p = Path(path)
        if not p.is_file():
            return None, [f"{label}: {p} is read by this step but is not on "
                          f"disk"]
        data = canonical_bytes(p, rule)
        if data is None:
            return None, [f"{label}: {p} could not be read"]
        d = _sha256_bytes(data)
        try:
            rel = os.path.relpath(p, Path(project))
        except ValueError:
            rel = str(p)
        pairs.append((f"{label}:{rel}", d))
        evidence.append(f"{label}={rel}@{d[:12]}"
                        + ("" if rule == "raw" else f" [{rule}]"))
    for name in sorted(knobs):
        val = "" if knobs[name] is None else str(knobs[name])
        pairs.append((f"knob:{name}", _sha256_bytes(val.encode("utf-8"))))
        evidence.append(f"{name}={val!r}")
    return _digest_pairs(pairs), evidence


def inputs_digest(project: Path, steps: Sequence[Dict[str, Any]], kind: str,
                  extra: Sequence[Path] = ()
                  ) -> Tuple[Optional[str], List[str]]:
    """sha256 over everything the SPAN reads, as it exists in this run.

    Returns `(digest, evidence)`. `digest` is None — meaning re-run — when the
    kind has no span, when a declared input resolves to nothing, or when a
    resolved file cannot be read. A declared input that is not there is not
    "no input": it is an unanswerable question.

    `extra` carries inputs the flow does not declare but the step demonstrably
    reads — the DEF a stream-out consumes, which other steps promote IN PLACE
    (`step_signoff_spef_repair`) and which no `required_inputs` entry names."""
    specs, _produced = span_input_specs(steps, kind)
    if not specs and not extra:
        return None, [f"no flow step in {kind}'s span declares an input that "
                      f"the span does not also produce, so there is nothing "
                      f"to hash"]
    pairs: List[Tuple[str, str]] = []
    evidence: List[str] = []
    seen: Set[str] = set()

    def _take(path: Path) -> Optional[str]:
        d = _sha256_file(path)
        if d is None:
            return None
        try:
            rel = os.path.relpath(path, Path(project))
        except ValueError:
            rel = str(path)
        if rel not in seen:
            seen.add(rel)
            pairs.append((rel, d))
            evidence.append(f"{rel}={d[:12]}")
        return d

    for spec, conditional in specs:
        hits: List[Path] = []
        for alt in (x.strip() for x in spec.split(" OR ")):
            hits = _glob_first(Path(project), alt)
            if hits:
                break
        if not hits:
            if conditional:
                # A VALUE, not a refusal — see `span_input_specs`. If the file
                # ever appears, this pair changes and the step re-runs.
                pairs.append((spec, "absent"))
                evidence.append(f"{spec}=absent(conditional producer)")
                continue
            return None, [f"declared input {spec!r} resolves to no file in "
                          f"this run, so this step's inputs cannot be hashed"]
        for h in hits:
            if _take(h) is None:
                return None, [f"declared input {h} could not be read"]
    for path in extra:
        p = Path(path)
        if not p.is_file():
            return None, [f"{p} is read by this step but is not on disk, so "
                          f"its inputs cannot be hashed"]
        if _take(p) is None:
            return None, [f"{p} could not be read"]
    if not pairs:
        return None, [f"{kind}'s span names no hashable input"]
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


#: (members, bindings, error) per module file, for the life of the process.
_MODULE_INDEX: Dict[Any, Tuple[Dict[str, str], Dict[str, str],
                                Optional[str]]] = {}


def _index_module(path: Path):
    """Parsed members + import bindings for one module, memoised.

    KEYED ON THE FILE'S IDENTITY, NOT ITS PATH, and that is a defect my own
    tests caught: a path-only key returned the PARSED-BEFORE members after the
    file had been edited, so `code_digest` computed twice in one process gave
    the same answer for different bytes — the cache silently asserting that
    nothing had changed. A stamp and a freshness check can share a process, so
    this is not only a test artefact."""
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = (str(path), 0, -1)
    hit = _MODULE_INDEX.get(key)
    if hit is None:
        members, err = _module_members(path)
        bindings = {} if err else _import_bindings(path)
        hit = (members, bindings, err)
        _MODULE_INDEX[key] = hit
    return hit


def _referenced_targets(segment: str, module: Path, bindings: Dict[str, str],
                        members: Dict[str, str], programs_dir: Path):
    """Every `(module_path, member_name)` a source segment can reach.

    Resolves three shapes: a bare name defined in THIS module; a bare name
    bound by `from X import f` (-> member `f` of module X); and `alias.attr`
    where `alias` is an imported module (-> member `attr` of that module). A
    module alias used WITHOUT an attribute cannot be narrowed, so it yields
    `(module, None)` and the caller takes that module whole — disclosed, and
    the conservative direction."""
    try:
        tree = ast.parse(segment)
    except SyntaxError:
        return set()
    out = set()

    def _mod_of(dotted: Optional[str]):
        if not dotted:
            return None
        return _resolve_module(programs_dir, dotted)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            alias = node.value.id
            dotted = bindings.get(alias)
            m = _mod_of(dotted)
            if m is not None:
                out.add((str(m), node.attr))
                continue
        if isinstance(node, ast.Name):
            nid = node.id
            if nid in members:
                out.add((str(module), nid))
                continue
            # `from X import f` -> member f of module X
            dotted = bindings.get(_FROM_FALLBACK + nid)
            m = _mod_of(dotted)
            if m is not None:
                out.add((str(m), nid))
                continue
            dotted = bindings.get(nid)
            m = _mod_of(dotted)
            if m is not None:
                # a module alias used bare: cannot be narrowed
                out.add((str(m), None))
    return out


def runner_code_closure(runner_path: Path, kind: str, programs_dir: Path = None
                        ) -> Tuple[Optional[str], List[str]]:
    """Digest of the FUNCTIONS this kind's step actually runs — across modules.

    r3 review (w18fc56v9) finding 3, and it is the coarseness R-0924-3 exists
    to remove. r3 walked the call graph INSIDE the runner but then hashed every
    imported module as a WHOLE FILE — so pnr's and gds's `code` contained the
    whole of `phase3_one_shot_runner.py` and the whole of
    `flow_compliance_check.py`, and an edit anywhere in either re-ran PnR and
    GDS. A per-kind key that every edit invalidates is the build key again,
    wearing a different name.

    The closure is now `(module, member)` pairs and it CROSSES module
    boundaries: the step's entry function, every runner function it
    transitively references, and — in each imported module — only the members
    that are actually reached. A module alias used without an attribute cannot
    be narrowed, so that module is taken whole and the evidence SAYS so; an
    unparseable module is taken whole for the same reason. Both are the
    conservative direction (more invalidation, never less).

    `flow_compliance_check` therefore enters a kind's identity only if that
    kind's own code reaches it — which is the reviewer's rule, enforced by
    construction rather than by a list."""
    seeds = KIND_RECIPE_SEEDS.get(kind)
    if not seeds:
        return None, [f"no recipe seed declared for kind {kind!r}"]
    runner_path = Path(runner_path)
    programs_dir = Path(programs_dir or runner_path.resolve().parent)
    members, bindings, err = _index_module(runner_path)
    if err:
        return None, [err]
    missing = [x for x in seeds if x not in members]
    if missing:
        return None, [f"recipe seed(s) {', '.join(missing)} are not defined in "
                      f"{runner_path.name} — the anchor moved and the closure "
                      f"cannot be computed, so nothing is reused"]

    seen: Set[Tuple[str, str]] = set()
    whole: Set[str] = set()
    notes: List[str] = []
    stack: List[Tuple[str, Optional[str]]] = [(str(runner_path), x)
                                              for x in seeds]
    while stack:
        mod_s, name = stack.pop()
        mod = Path(mod_s)
        mems, binds, merr = _index_module(mod)
        if merr:
            whole.add(mod_s)
            continue
        if name is None:
            whole.add(mod_s)
            continue
        if (mod_s, name) in seen or name not in mems:
            continue
        seen.add((mod_s, name))
        for tgt in _referenced_targets(mems[name], mod, binds, mems,
                                       programs_dir):
            if tgt[1] is None:
                whole.add(tgt[0])
            elif tgt not in seen:
                stack.append(tgt)
        # A program this member runs BY FILE PATH is code it runs — and it is
        # narrowed the same way everything else is. r3 review finding 3 asked
        # that `flow_compliance_check` enter a kind's identity only if that
        # kind's code reaches it; it is dispatched by path from the runner, so
        # it DOES — but what enters is its own entry-point closure, not its
        # 10k lines. A dispatched program with no recognisable entry point
        # cannot be narrowed and is taken whole, disclosed.
        for lit in _PY_PATH_LITERAL_RE.findall(mems[name]):
            m = _resolve_module(programs_dir, Path(lit).stem)
            if m is None:
                continue
            sub_mems, _sb, sub_err = _index_module(m)
            entry = next((e for e in ("main", "cli", "run")
                          if e in sub_mems), None) if not sub_err else None
            if entry is None:
                whole.add(str(m))
            else:
                stack.append((str(m), entry))

    pairs: List[Tuple[str, str]] = []
    for mod_s, name in sorted(seen):
        mems, _b, _e = _index_module(Path(mod_s))
        try:
            rel = str(Path(mod_s).resolve().relative_to(
                programs_dir.resolve()))
        except ValueError:
            rel = Path(mod_s).name
        pairs.append((f"{rel}::{name}",
                      _sha256_bytes(mems[name].encode("utf-8"))))
    for mod_s in sorted(whole):
        d = _sha256_file(Path(mod_s))
        if d is None:
            return None, [f"{mod_s} could not be read"]
        try:
            rel = str(Path(mod_s).resolve().relative_to(
                programs_dir.resolve()))
        except ValueError:
            rel = Path(mod_s).name
        pairs.append((f"whole:{rel}", d))
    notes.append(
        f"{len(seen)} function(s)/constant(s) across "
        f"{len({m for m, _ in seen})} module(s) reachable from "
        f"{', '.join(seeds)}"
        + (f"; {len(whole)} module(s) taken whole (unnarrowable alias or "
           f"file-path dispatch)" if whole else ""))
    return _digest_pairs(pairs), notes


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
                # REVIEW FINDING 3 (r1): binding the name to `"<module>.<name>"`
                # and nothing else meant `from _route_wire_transaction import f`
                # resolved to a module path that does not exist, so
                # `_route_wire_transaction.py`, `pad_signal_route_repair.py`
                # (the Tcl emitters behind every PnR script) and
                # `_pdk_via_analyzer.py` were OUTSIDE pnr's code identity — a
                # landed fix to any of them would have been silently skipped.
                # `X.f` is tried first because `from _ppa import area` really
                # does name a submodule; `X` is the fallback and is the case
                # that was missing.
                out[a.asname or a.name] = f"{node.module}.{a.name}"
                out.setdefault(_FROM_FALLBACK + (a.asname or a.name),
                               node.module)
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
    """Digest of the SPAN's declared `programs:` plus their direct imports."""
    producers = steps_in_span(steps, kind)
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
    """The code this step RUNS — the cross-module function-level closure.

    The span's declared `programs:` are NOT added wholesale any more. r3 review
    finding 3: doing that pulled `flow_compliance_check.py` in whole, so every
    edit to it re-ran PnR and GDS. A declared program belongs in a kind's
    identity only if that kind's own code REACHES it — and if the step runs it,
    the closure finds it, including by file-path dispatch. Enforced by
    construction rather than by a list."""
    return runner_code_closure(Path(runner_path), kind, Path(programs_dir))


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
    """`(field, path)` for every PDK file this flow declares, in order.

    THE SOURCE, NOT THE DERIVATION. REVIEW FINDING 4 (r1): `step_pnr` stages a
    VIA-legalised tech LEF into the RUN directory and MUTATES the shared
    `PdkConfig` to point at it (`phase3_one_shot_runner:33391`, which also sets
    `tech_lef_source` to the original). The stamp is written after the step, so
    the recorded PDK was the derived file — which differs every run, so the PDK
    component never matched and PnR was NEVER reused. `<field>_source` is the
    plugin's own record of what the field was before the flow touched it, so it
    is preferred wherever it is set: a file this run produced is an OUTPUT, and
    an output is not evidence about whether the step that made it should run."""
    out: List[Tuple[str, str]] = []
    for field in _PDK_FIELDS:
        src = getattr(pdk, f"{field}_source", None)
        val = src or getattr(pdk, field, None)
        if val:
            out.append((field, str(val)))
    return out


def pdk_files_checked(pdk: Any, project: Optional[Path]
                      ) -> Tuple[List[Tuple[str, str]], List[str]]:
    """`pdk_files`, plus a refusal for any path that is this RUN's own output.

    BELT AND BRACES for finding 4, and it does not depend on the PDK's layout.
    `<field>_source` is set by the one derivation we know about (the VIA-patch
    legalizer), and the consumers that read it key off `/libs.ref/` — a
    convention, not a guarantee. So the structural fact is checked directly
    instead: a PDK path INSIDE THE PROJECT is a file this run produced, and a
    file this run produced is not evidence about whether the run should
    happen. With no `<field>_source` to fall back on, that is unanswerable and
    refuses rather than hashing the derivation."""
    files = pdk_files(pdk)
    if project is None:
        return files, []
    bad: List[str] = []
    try:
        root = Path(project).resolve()
    except OSError:
        return files, []
    try:
        design = (root / "input").resolve()
    except OSError:
        design = None
    for field, val in files:
        try:
            rp = Path(val).resolve()
            if not rp.is_relative_to(root):
                continue
            # r3 review finding 5: a PDK the DESIGN STAGES under `input/` is a
            # design input by the same rule everything else here follows —
            # `input/` is the design's, and refusing it made every staged-PDK
            # run (asap7 measured) permanently un-fresh. Only a path under the
            # run's OUTPUT tree is this run's own derivation.
            if design is not None and rp.is_relative_to(design):
                continue
            bad.append(f"{field}={val} is inside the run's OUTPUT tree, so it "
                       f"is this run's own derivation and no {field}_source "
                       f"names what it came from")
        except (OSError, ValueError):
            continue
    return files, bad


def pdk_digest(pdk: Any, hasher: Any = None,
               project: Optional[Path] = None
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
    want, derived = pdk_files_checked(pdk, project)
    if derived:
        return None, derived
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
                 image_digest: Optional[str] = None, pdk_hasher: Any = None,
                 extra_inputs: Sequence[Path] = (),
                 inputs: Optional[Sequence[Tuple[str, Any, str]]] = None,
                 knobs: Optional[Dict[str, str]] = None
                 ) -> Tuple[Dict[str, Optional[str]], Dict[str, List[str]]]:
    """This build's identity for `kind`, plus per-component evidence."""
    steps = load_steps(Path(flow_yaml))
    why: Dict[str, List[str]] = {}
    ident: Dict[str, Optional[str]] = {}
    if inputs is not None:
        # THE CALLER RESOLVED THEM, the way the step does. r2 review finding A.
        ident["inputs"], why["inputs"] = resolved_inputs_digest(
            Path(project), inputs, dict(knobs or {}))
    else:
        ident["inputs"], why["inputs"] = inputs_digest(
            Path(project), steps, kind, extra_inputs)
    ident["code"], why["code"] = code_digest(
        Path(runner_path), Path(programs_dir), steps, kind)
    ident["tools"], why["tools"] = tools_digest(
        Path(project), kind, image_digest)
    ident["pdk"], why["pdk"] = pdk_digest(pdk, pdk_hasher, Path(project))
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
