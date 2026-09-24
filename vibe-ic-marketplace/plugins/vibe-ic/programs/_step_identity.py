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


















def code_from_recording(recording) -> Tuple[Optional[str], List[str]]:
    """`code` at STAMP time: a digest over what the step actually ran."""
    if not recording:
        return None, ["nothing was recorded for this step"]
    try:
        import _step_recorder as _sr  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return None, [f"the recorder is unavailable ({type(exc).__name__})"]
    n_fn = sum(len(v) - 1 for v in recording.values())
    return _sr.digest_of(recording), [
        f"{n_fn} function(s) recorded across {len(recording)} file(s) "
        f"(plus each file's module body)"]


def code_from_stored(stored, programs_dir: Path
                     ) -> Tuple[Optional[str], List[str]]:
    """`code` at CHECK time: RE-DERIVE the recorded keys from CURRENT source.

    The step has not run, so there is nothing to record — what exists is the
    recording the LAST run left, and the question is whether the code it names
    still says the same thing. A recorded function that has changed, or that
    is GONE from the file, or a module body that moved, all make this differ.
    No recording at all means no cache: a step whose code was never recorded
    is not a step anyone can prove current."""
    if not stored:
        return None, ["the cached artefact carries no recording of what its "
                      "step ran, so its code cannot be compared"]
    try:
        import _step_recorder as _sr  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return None, [f"the recorder is unavailable ({type(exc).__name__})"]
    now, err = _sr.rederive(stored, Path(programs_dir))
    if err:
        return None, [err]
    missing = [f"{rel}:{k}" for rel, d in now.items()
               for k, v in d.items() if v == "ABSENT"]
    note = f"{len(now)} recorded file(s) re-derived"
    if missing:
        note += f"; {len(missing)} recorded function(s) no longer in source"
    return _sr.digest_of(now), [note]




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






def pdk_field_values(pdk: Any) -> List[Tuple[str, str]]:
    """EVERY resolved field of the PdkConfig the step RECEIVED.

    r4 review finding 2: the recipe-shaping fields are computed in `main()` —
    `_detect_pdk` -> `_pdk_config_from_registry` -> `_derive_tapcell_master` —
    which is outside every seed closure, and the old `pdk` component hashed
    only five FILE paths. So an edit to `pdk_registry.json` (`clk_buf_cell`,
    `pdn_straps`, `pdn_ring`) or a change in the tap-master rule altered the
    DEF with all four identity components unchanged: a regression against the
    whole-runner key it replaced.

    The object the step was handed is the authority, so all of its fields are
    hashed — including `macro_*` lists, which also closes the macro half of
    finding 3 without a glob."""
    out: List[Tuple[str, str]] = []
    names = getattr(pdk, "__dataclass_fields__", None)
    if names is None:
        names = [n for n in dir(pdk)
                 if not n.startswith("__") and not callable(getattr(pdk, n,
                                                                   None))]
    for name in sorted(names):
        try:
            val = getattr(pdk, name)
        except Exception:  # noqa: BLE001
            continue
        if callable(val):
            continue
        try:
            out.append((name, json.dumps(val, sort_keys=True, default=str)))
        except Exception:  # noqa: BLE001
            out.append((name, repr(val)))
    return out


def _paths_in(values: Sequence[Tuple[str, str]]) -> List[str]:
    """Every value that looks like a filesystem path, in field order."""
    out: List[str] = []
    for _name, raw in values:
        for tok in re.findall(r'"([^"]{3,})"|^([^"\s]{3,})$', raw,
                              re.MULTILINE):
            cand = tok[0] or tok[1]
            if cand.startswith("/") and cand not in out:
                out.append(cand)
    return out


def pdk_digest(pdk: Any, hasher: Any = None,
               project: Optional[Path] = None
               ) -> Tuple[Optional[str], List[str]]:
    """The PDK identity: every field the step received, plus the BYTES of
    every file among them.

    WHERE THE FILES ACTUALLY ARE. `PdkConfig.liberty` and its siblings are
    documented as "path inside container (or host, if absolute exists)", and
    on the measured configuration they are container-ONLY: the host has no
    `/foss/pdks` at all. Hashing them host-side made the component permanently
    uncomputable and the whole change "always re-run" — worse than what it
    replaced. So the caller supplies a `hasher` that reads them where they
    live.

    A path under the run's OUTPUT tree is this run's own derivation and
    refuses; a path the design STAGED under `input/` is a design input and is
    hashed (r3 review finding 5 — asap7 measured)."""
    if pdk is None:
        return None, ["no PDK configuration in hand"]
    fields = pdk_field_values(pdk)
    if not fields:
        return None, ["the PDK configuration exposes no field"]
    # Prefer `<field>_source` over a field the FLOW derived in-run.
    by_name = dict(fields)
    resolved: List[Tuple[str, str]] = []
    for name, raw in fields:
        if name.endswith("_source"):
            continue
        src = by_name.get(f"{name}_source")
        resolved.append((name, src if src and src != "null" else raw))
    paths = _paths_in(resolved)
    digests: Dict[str, str] = {}
    if hasher is not None and paths:
        try:
            got = hasher(paths)
        except Exception as exc:  # noqa: BLE001 — a failed probe is not a pass
            return None, [f"the PDK hasher failed ({type(exc).__name__}: "
                          f"{exc}), so the PDK cannot be proven unchanged"]
        if isinstance(got, dict):
            digests.update({str(k): str(v) for k, v in got.items() if v})
    pairs: List[Tuple[str, str]] = [
        (f"field:{n}", _sha256_bytes(v.encode("utf-8"))) for n, v in resolved]
    unresolved: List[str] = []
    try:
        root = Path(project).resolve() if project is not None else None
        design = (root / "input").resolve() if root is not None else None
    except OSError:
        root = design = None
    for path in paths:
        # ASKED BEFORE IT IS READ. A file this run PRODUCED is not evidence
        # about whether the run should happen, and it is perfectly readable —
        # so testing readability first would hash it and never refuse.
        try:
            rp = Path(path).resolve()
            if root is not None and rp.is_relative_to(root) and (
                    design is None or not rp.is_relative_to(design)):
                unresolved.append(
                    f"{path} is inside the run's OUTPUT tree, so it is this "
                    f"run's own derivation")
                continue
        except (OSError, ValueError):
            pass
        d = digests.get(path)
        if d is None:
            q = Path(path)
            d = _sha256_file(q) if q.is_file() else None
        if d is None:
            unresolved.append(f"{path} could not be read")
            continue
        pairs.append((f"file:{path}", d))
    if unresolved:
        return None, [f"the PDK cannot be proven unchanged: "
                      f"{'; '.join(unresolved)}"]
    return _digest_pairs(pairs), [
        f"{len(resolved)} PDK field(s) and {len(paths)} file(s)"]


# --------------------------------------------------------------------------
# the identity, and the comparison
# --------------------------------------------------------------------------
def identity_now(*, project: Path, kind: str, runner_path: Path,
                 programs_dir: Path, flow_yaml: Path, pdk: Any = None,
                 image_digest: Optional[str] = None, pdk_hasher: Any = None,
                 extra_inputs: Sequence[Path] = (),
                 inputs: Optional[Sequence[Tuple[str, Any, str]]] = None,
                 knobs: Optional[Dict[str, str]] = None,
                 recording: Any = None, stored_recording: Any = None,
                 stored_tools: Optional[str] = None
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
        # There is no flow-derived fallback any more. Round-3 review finding 1
        # showed that deriving a step's inputs from what the FLOW DECLARES is
        # a different set from what the STEP READS, and the caller now resolves
        # them by calling the step's own resolvers. A caller that supplies none
        # has not established the read-set, and that is a refusal.
        ident["inputs"], why["inputs"] = (
            None, ["no caller resolved this step's read-set"])
    # CODE — what the step RAN, never a static approximation of it. The
    # closure stays available as a disclosed cross-check but is not the
    # authority (R-0924-3 r5).
    if recording is not None:
        ident["code"], why["code"] = code_from_recording(recording)
    elif stored_recording is not None:
        ident["code"], why["code"] = code_from_stored(
            stored_recording, Path(programs_dir))
    else:
        ident["code"], why["code"] = (
            None, ["no recording of what this step ran, and none stored"])
    # TOOLS — the versions this step PROBED, carried in the stamp, plus the
    # live image identity. r4 review finding 4: re-reading `provenance.jsonl`
    # made this move on every no-op re-run, because
    # `step_canonicalize_artefacts` keeps APPENDING to that ledger after the
    # stamps, with version-less reconstructed rows. The ledger is a record of
    # the past and kept growing; a freshness key may not be read from it.
    if stored_tools:
        ident["tools"], why["tools"] = (
            _digest_pairs((("probed", stored_tools),
                           ("image", image_digest or ""))) if image_digest
            else None,
            ["the probed tool versions carried in the stamp, plus the live "
             "image identity"] if image_digest else
            ["the container image cannot be named, so the tool identity "
             "cannot be established"])
    else:
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
                  why: Optional[Dict[str, List[str]]] = None,
                  extra: Optional[Dict[str, Any]] = None) -> None:
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
        if extra:
            # The RECORDING itself lives here: freshness re-derives its keys
            # from current source, so the stamp has to carry it, not just a
            # digest of it.
            rec.update({k: v for k, v in extra.items() if v is not None})
        doc[kind] = rec
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    except Exception:  # nosec — sidecar is best-effort, like its predecessor
        pass
