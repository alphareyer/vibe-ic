#!/usr/bin/env python3
"""gds_xor_check — stream-out / finishing fidelity, as a tapeout checklist means it.

R-0915-129, metric 2. `tapeout_docs_gen` requires
`design__xor_difference__count`, and `signoff_metrics_aggregate` answered
NOT_MEASURED with "no step of this flow produces a GDS-vs-layout XOR report" --
literally true: `grep -c -i xor` over the flow yaml was ZERO. That is a GAP IN THE
FLOW, not a property of the problem: KLayout in the pinned image has `xor`, so the
comparison is producible and declaring it NOT_APPLICABLE_BY_STRUCTURE would have
been laundering.

WHAT THE MEASUREMENT IS. The SHIPPED GDS has been through finishing -- density
fill, seal ring, grid snap -- and every one of those wrote IN PLACE: measured on
spm run21, `cmp_fill_emit.json` records `gds_in == gds_out ==
phase3/stage3/pnr/spm.gds`. So no pre-finishing GDS survives to compare against,
and the honest reference is a FRESH STREAM-OUT of the same routed database the
shipped GDS came from. The run records exactly which that was:
`pad_ring_route_evidence.json` gives `gds_source_def` and its sha256 beside the
GDS's own sha256, so the comparison binds to the identity the run attested.

THE PARTITION IS THE POINT. A raw XOR of those two is NOT zero and must not be:
finishing added geometry on purpose. So the layers the finishing steps DECLARE
they added are separated out and disclosed with their own counts, and
`design__xor_difference__count` is the total on every OTHER layer -- where a
difference means the stream-out changed the design, which is what a checklist
means by fidelity. Both declarations are read from the run's own records:
  * fill      `die_density_fill.json` -> `fill.owned_layers`  (measured [34, 36,
              42, 46, 81] on run21)
  * seal ring `die_finishing.json` -> `seal_ring.ring_check.added_layers`
              (measured 14 layer/datatype pairs on run21)
A layer the finishing steps did NOT declare is a design layer, so an undeclared
addition cannot hide in the disclosed bucket.

FAIL on any design-layer difference. The finishing buckets never fail here --
their counts are evidence for a reader, and the steps that own them have their own
gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
# THE DECLARED REPORT IS WRITTEN ATOMICALLY, WITH NO FALLBACK. An earlier
# revision wrapped this import in a try/except and fell back to
# `out.write_text(...)`, which is the shape vibe-ic#1082's ratchet refuses --
# `atomic_artifact_write_check` named it here, at the fallback line, as the one
# NEW non-atomic declared-report write in 1452 programs. The fallback was worse
# than a missing import: a reader of this file would believe the write was
# atomic while the path that actually ran on any host where the import failed
# left a half-written receipt behind, and `signoff_metrics_aggregate --check`
# reads this receipt. An unimportable helper is a broken installation and must
# say so at import time, not degrade the guarantee silently.
from _atomic_artefact import write_json as _atomic_write_json
import _stream_input_record as _sir  # noqa: E402

GATE = "gds_xor_check"
REPORT_REL = "reports/phase3/gds_xor.json"
#: The run's own attestation of which DEF the shipped GDS was streamed from.
ROUTE_EVIDENCE_REL = "reports/phase3/pad_ring_route_evidence.json"
FILL_REL = "reports/phase3/die_density_fill.json"
#: The CMP/dummy fill emitter's own receipt. It declares the DATATYPE its fill
#: lands on (`layers[].fill_datatype`), which is what makes the fill exemption
#: datatype-precise instead of swallowing a whole layer.
CMP_FILL_REL = "reports/phase3/cmp_fill_emit.json"
FINISHING_REL = "reports/phase3/die_finishing.json"

#: `layer/datatype` as the finishing records spell it.
_PAIR_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


def _json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fill_datatypes(project: Path) -> Set[int]:
    """The datatype(s) the fill emitter DECLARES it writes on, read from its receipt."""
    rec = _json(project / CMP_FILL_REL) or {}
    out: Set[int] = set()
    for row in (rec.get("layers") or []):
        dt = row.get("fill_datatype") if isinstance(row, dict) else None
        if isinstance(dt, int) and not isinstance(dt, bool):
            out.add(dt)
    return out


def declared_finishing_layers(project: Path) -> Tuple[Set[int], Set[Tuple[int, int]],
                                                      Dict[str, Any]]:
    """({fill layer numbers}, {seal (layer, datatype) pairs}, provenance).

    Read from the run's OWN records, never from a layer-name convention: a layer
    is exempt from the design-layer count only because a finishing step SAID it
    added it.
    """
    prov: Dict[str, Any] = {"fill": None, "seal_ring": None}
    fill_pairs: Set[Tuple[int, Optional[int]]] = set()
    seal_pairs: Set[Tuple[int, int]] = set()

    # THE GENERATOR'S WHOLE WRITE SET, not one pass of it. `fill.owned_layers` is
    # the METAL pass's layers; the die-wide generator also runs a COMP pass and a
    # Poly2 pass, and the run's own receipt says so in the same document:
    # `fill.probe.layers_the_whole_generator_writes` = [22,30,34,36,42,46,81] with
    # `layers_per_pass` naming fill_comp.rb -> [22], fill_poly2.rb -> [30],
    # fill_metal.rb -> the five metals. Reading only `owned_layers` left 22/4 and
    # 30/4 unattributed, and this program then reported 85,581 differences as a
    # DESIGN fidelity finding when both counts were the run's own fill cells
    # (COMP_fill_cell 75,175 and Poly2_fill_cell 10,406, from the route
    # attestation's reachable_structure_references). Widest DECLARED set, then.
    fill = _json(project / FILL_REL)
    fblock = (fill or {}).get("fill") or {}
    whole = (fblock.get("probe") or {}).get("layers_the_whole_generator_writes")
    owned = fblock.get("owned_layers")
    layers = whole if isinstance(whole, list) else owned
    field = ("fill.probe.layers_the_whole_generator_writes"
             if isinstance(whole, list) else "fill.owned_layers")
    if isinstance(layers, list):
        nums = {int(x) for x in layers
                if isinstance(x, int) and not isinstance(x, bool)}
        # DATATYPE-PRECISE WHERE THE EMITTER DECLARES ONE. The fill lands on a
        # datatype the CMP emitter names (4 on run21), so exempting the whole layer
        # would also excuse 22/0 and 30/0 -- real design layers. Pairing each
        # declared layer with each declared fill datatype keeps those checked; with
        # no datatype declared the exemption stays layer-wide, as before, and the
        # provenance says which of the two was used.
        dts = fill_datatypes(project)
        if dts:
            fill_pairs = {(n, d) for n in nums for d in dts}
        else:
            fill_pairs = {(n, None) for n in nums}
        prov["fill"] = {"source": FILL_REL, "field": field,
                        "layers": sorted(nums),
                        "datatypes": sorted(dts) if dts else None,
                        "datatype_source": CMP_FILL_REL if dts else None,
                        "granularity": "layer/datatype" if dts else "layer-wide"}

    fin = _json(project / FINISHING_REL)
    added = (((fin or {}).get("seal_ring") or {}).get("ring_check")
             or {}).get("added_layers")
    if isinstance(added, list):
        for item in added:
            m = _PAIR_RE.match(str(item))
            if m:
                seal_pairs.add((int(m.group(1)), int(m.group(2))))
        prov["seal_ring"] = {"source": FINISHING_REL,
                             "field": "seal_ring.ring_check.added_layers",
                             "pairs": [f"{l}/{d}" for l, d in sorted(seal_pairs)]}
    return fill_pairs, seal_pairs, prov


#: Where the flow itself puts the two artefacts, for a run that attested no pair.
#: These are the SAME strings the flow declares -- step 37's gate hard-requires
#: `phase3/stage4/gds/*.gds` and step 21's requires `phase3/stage3/pnr/routed.def`
#: -- so a project missing either has already FAILED at the step that owed it.
CANONICAL_GDS_GLOB = "phase3/stage4/gds/*.gds"
CANONICAL_DEF_REL = "phase3/stage3/pnr/routed.def"


def shipped_and_source(project: Path) -> Tuple[Optional[Path], Optional[Path],
                                               Dict[str, Any]]:
    """(shipped GDS, the routed DEF it was streamed from, the run's attestation).

    PREFERRED SOURCE: `pad_ring_route_evidence.json`, which records the GDS path
    and sha256 beside `gds_source_def` and ITS sha256. Taking them from there
    rather than by globbing binds the comparison to the pair the run attested, and
    makes a tree whose GDS has since changed detectable.

    BUT THE ATTESTATION IS THE BINDING, NOT THE SUBJECT, and conflating the two
    was a real defect in the first shape of this step. The runner writes that file
    only under `_chip_path_requests_pad_ring(project)`, so on every design that is
    not on the chip pad-ring path there is no attestation -- and the first draft
    made the STEP conditional on it, which `flow_condition_reachability_check`
    named for what it is: a SELF-DISABLING CONDITION on an artefact the flow
    itself produces, where "the file is missing" and "the fidelity check was never
    made" are indistinguishable to every reader downstream. Its absence is loud
    NOWHERE (T7=None, T3=None, T5=None, measured).

    So the subject is the TWO ARTEFACTS, which is what the fidelity question is
    actually about, and both have a loud absence: the shipped GDS is a hard
    `files_exist` in step 37's own gate and the routed DEF is one in step 21's, so
    a project missing either FAILED there and never reaches here looking clean.
    The attestation, when the run wrote one, still decides the pairing and still
    refuses on a sha256 that no longer holds; when it did not, the canonical flow
    paths are used and `att["binding"]` says so, so no reader can mistake a
    convention-bound comparison for an attested one.
    """
    ev = _json(project / ROUTE_EVIDENCE_REL) or {}
    gds_rel = ((ev.get("gds_evidence") or {}).get("path"))
    def_rel = ev.get("gds_source_def")
    att = {
        "source": ROUTE_EVIDENCE_REL,
        "gds": gds_rel,
        "gds_sha256_recorded": (ev.get("gds_evidence") or {}).get("sha256"),
        "def": def_rel,
        "def_sha256_recorded": ev.get("gds_source_def_sha256"),
        "streamout_engine": (ev.get("gds_evidence") or {}).get("streamout_engine"),
        "binding": "attested" if isinstance(gds_rel, str) else "",
    }
    gds = (project / gds_rel) if isinstance(gds_rel, str) else None
    dfile = (project / def_rel) if isinstance(def_rel, str) else None

    if gds is None or not gds.is_file():
        # SORTED, so two candidate GDS files pick the same one on every host: an
        # unordered glob would make the subject depend on directory order.
        found = sorted(project.glob(CANONICAL_GDS_GLOB))
        if found:
            gds = found[0]
            att.update(binding="flow-canonical",
                       gds=str(gds.relative_to(project)),
                       gds_sha256_recorded=None,
                       binding_reason=(
                           f"{ROUTE_EVIDENCE_REL} attested no shipped GDS "
                           f"(this design is not on the chip pad-ring path), so "
                           f"the subject is the flow's own declaration "
                           f"{CANONICAL_GDS_GLOB} -- whose absence FAILs step 37"))
    if dfile is None or not dfile.is_file():
        cand = project / CANONICAL_DEF_REL
        if cand.is_file():
            dfile = cand
            att.setdefault("binding_reason", "")
            att.update(binding=att["binding"] or "flow-canonical",
                       def_sha256_recorded=None)
            att["def"] = CANONICAL_DEF_REL
    return gds, dfile, att


#: The boundary artefact the gds step retains: the stream-out BEFORE fill, seal
#: ring and snap. Same shape as the boundary DEFs #2484 keeps, and for the same
#: reason -- every finishing pass writes IN PLACE, so a "before" only exists if it
#: was kept.
PREFINISH_REL_FMT = "phase3/stage3/pnr/{top}.prefinish.gds"
#: A GLOB, NOT ONE NAME, and for the same reason `STREAMOUT_LOG_GLOB` below is one.
#:
#: The runner retains `{top}.prefinish.gds` where `top` is the name PnR ran under;
#: this checker resolved `{top}` from the DEF FILE'S STEM. Those are not the same
#: string. MEASURED on spm run22: the shipped GDS is `spm.gds`, the source DEF is
#: `spm.def` -- so this looked for `phase3/stage3/pnr/spm.prefinish.gds` -- while
#: the boundary the run actually kept is `phase3/stage3/pnr/chip_top.prefinish.gds`
#: (the runner's top was `chip_top`, and the front door's own advisory recorded
#: "phase3 auto-derived top='spm' from --ic-name (no chip_top module)").
#:
#: The names could not meet, so the RETAINED boundary was never used and every run
#: fell back to a re-stream. That fallback is the expensive path this artefact
#: exists to avoid, and a re-stream missing one library produces a CONFIDENT WRONG
#: ANSWER about the design -- 776,403 then 348,392 phantom differences, measured,
#: and neither of them about that chip.
#:
#: One boundary per run, so a glob is exact rather than lenient: if it ever matches
#: more than one, `resolve_reference` refuses instead of guessing.
PREFINISH_GLOB = "phase3/stage3/pnr/*.prefinish.gds"
#: The run's own retained stream-out recipe and its transcript. A fallback that
#: re-streams must use THE RUN'S recipe, not a hand-rolled read: that mistake
#: produced 776,403 differences across 29 design layers on run21 -- placement boxes
#: against real standard-cell geometry -- and it was mine, not the design's.
STREAMOUT_SCRIPT_REL = "phase3/stage3/pnr/stream_out.py"
#: A GLOB, NOT ONE NAME, because the two stream-out engines write two names:
#: KLayout writes `stream_out.log` and Magic writes `<top>.magic_stream_out.log`.
#: Reading only the KLayout name meant a Magic-streamed run recovered NO library
#: hints at all and its re-streamed reference came out thin -- caught, but caught
#: as a refusal from `reference_is_faithful` rather than answered. This is also
#: the spelling step 37 declares and its own gate asserts, so the read, the
#: declaration and the assertion are the same set.
STREAMOUT_LOG_GLOB = "phase3/stage3/pnr/*stream_out.log"

#: The three field lines of the stream-out transcript that name a library input.
#:
#: ANCHORED AT BOTH ENDS, WITH A CLOSED FIELD TYPE, AND THAT IS WHAT MAKES THE
#: POLARITY QUESTION NOT ARISE HERE. Every one of these lines is written by the
#: run's own stream-out recipe (`phase3_one_shot_runner`, the stream-out body)
#: with `print(f"<TOKEN> <verb phrase>: {value}")`, from a value it had already
#: resolved -- so the grammar has no free-text field in which a denial could be
#: spelled. The recipe DOES say the negative, and it says it in DIFFERENT
#: productions that these patterns cannot reach:
#:     "LEFDEF_MAP not applied (none configured) - legacy numbering"
#:     "LEFDEF_MAP_CONFIGURED_BUT_ABSENT: ..."
#:     "MACRO_GDS merged (no DEF master matched): <path>"
#:     "CELL_GDS manual-substituted <n> std cell(s)"        (no ": <path>" field)
#: none of which matches `<TOKEN> <the exact verb>: <\S+>$`. A non-match leaves
#: the key OUT of the env dict, which is exactly what the denial means -- the
#: re-stream then passes no such variable and reads what the run read.
_LOG_PATTERNS = (
    ("LEFDEF_MAP", re.compile(r"^LEFDEF_MAP applied:\s*(\S+)\s*$", re.M), False),
    ("CELL_GDS", re.compile(r"^CELL_GDS manual-substitute \(post-read\):\s*(\S+)\s*$", re.M), False),
    ("MACRO_GDS", re.compile(r"^MACRO_GDS manual-substituted \d+ macro\(s\):\s*(\S+)\s*$", re.M), True),
)


def restream_env_from_transcript(project: Path) -> Tuple[Dict[str, str], List[str]]:
    """({env the run's stream-out used}, the transcript lines that said so).

    READ, not guessed. The run keeps `stream_out.log`, and it names the library
    inputs it resolved -- the layermap it applied, the std-cell GDS it substituted
    229 cells from, the two macro GDS files it substituted 16 macros from. Those
    are container-side PDK paths and must NOT be translated again, which is why
    only the project-side keys go in `path_keys` at the call.
    """
    env: Dict[str, str] = {}
    cited: List[str] = []
    # NEWEST LAST, and sorted so two candidates pick the same one on every host.
    # A DRC re-stream leaves a second Magic transcript beside the first, and the
    # library set that matters is the one the SHIPPED GDS came from.
    found = sorted(project.glob(STREAMOUT_LOG_GLOB),
                   key=lambda q: (q.stat().st_mtime, q.name))
    if not found:
        return env, cited
    log = found[-1]
    rel = log.relative_to(project).as_posix()
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return env, cited
    for key, pattern, many in _LOG_PATTERNS:
        found = pattern.findall(text)
        if not found:
            continue
        # ";" IS THE SEPARATOR THE SCRIPT PARSES, not whitespace: the recipe
        # reads MACRO_GDS as `os.environ.get('MACRO_GDS','').split(';')`
        # (phase3_one_shot_runner.py, the stream-out body). Joining with a space
        # hands it ONE unopenable path, both macro libraries are silently lost,
        # and the only symptom is a reference that is too small.
        env[key] = ";".join(dict.fromkeys(found)) if many else found[0]
        cited.append(f"{rel}: {key} = {env[key][:120]}")
    return env, cited


#: The KLayout batch script. Streams the routed DEF, XORs it against the shipped
#: GDS and prints one machine-readable line per differing layer. Printing rather
#: than writing a database: the counts are the evidence and a reader should not
#: have to open a layout to get them.
_XOR_RB = r'''
begin
  ship = RBA::Layout::new
  ship.read(ENV["XOR_SHIPPED"])
  ref  = RBA::Layout::new
  ref.read(ENV["XOR_REFERENCE"])
  top_s = ship.top_cell
  top_r = ref.top_cell
  if top_s.nil? || top_r.nil?
    puts "XOR_ERROR no top cell"
    exit 3
  end
  puts "XOR_TOP shipped=#{top_s.name} reference=#{top_r.name}"
  # THE FAITHFULNESS CENSUS, printed before any difference is counted. A
  # reference that is missing libraries produces a CONFIDENT WRONG ANSWER, and
  # nothing in the difference counts reveals it -- measured twice on run21: a bare
  # DEF read gave 776,403 differences and a re-stream missing cell substitution
  # gave 348,392, and only the reference's SIZE (8.3 MB against a shipped 102.9 MB)
  # gave either away. So the census is evidence the caller checks, not decoration.
  def census(ly)
    shapes = 0
    ly.each_cell { |c| ly.layer_indexes.each { |i| shapes += c.shapes(i).size } }
    [ly.cells.to_s, shapes.to_s]
  end
  sc, ss = census(ship)
  rc2, rs = census(ref)
  puts "XOR_CENSUS shipped_cells=#{sc} shipped_shapes=#{ss} reference_cells=#{rc2} reference_shapes=#{rs}"
  layers = {}
  ship.layer_indexes.each { |i| li = ship.get_info(i); layers[[li.layer, li.datatype]] = true }
  ref.layer_indexes.each  { |i| li = ref.get_info(i);  layers[[li.layer, li.datatype]] = true }
  layers.keys.sort.each do |pair|
    lay, dt = pair
    a = RBA::Region::new
    b = RBA::Region::new
    is = ship.find_layer(lay, dt)
    ir = ref.find_layer(lay, dt)
    a.insert(top_s.begin_shapes_rec(is)) unless is.nil?
    b.insert(top_r.begin_shapes_rec(ir)) unless ir.nil?
    d = a ^ b
    puts "XOR_LAYER #{lay}/#{dt} #{d.count}"
  end
  puts "XOR_DONE"
end
'''


def run_xor(runner, scratch: Path, shipped: Path, reference: Path,
            timeout: int) -> Tuple[int, str, str]:
    """Execute the XOR script, translating both paths for the runner.

    THE SCRIPT LIVES IN `scratch`, which is inside the project, because a
    container runner only reaches what its bind-mounts cover: a script written to
    the host's own /tmp produced
    `ERROR: Unable to open file: /tmp/.../xor.rb (errno=2)`. The paths are passed
    through the ENVIRONMENT and named in `path_keys` -- that is how
    `KLayoutRunner.run` translates a host path to its in-container spelling; ARGV
    is not plumbed at all.
    """
    script = scratch / "xor.rb"
    script.write_text(_XOR_RB)
    return runner.run(script, {"XOR_SHIPPED": shipped, "XOR_REFERENCE": reference},
                      path_keys=("XOR_SHIPPED", "XOR_REFERENCE"), timeout=timeout)


_CENSUS_RE = re.compile(
    r"^XOR_CENSUS shipped_cells=(\d+) shipped_shapes=(\d+) "
    r"reference_cells=(\d+) reference_shapes=(\d+)$", re.M)

#: How much smaller than the shipped layout a reference may be before it is
#: refused as unfaithful. The pre-finishing reference is legitimately smaller --
#: fill and seal ring add geometry -- but not by an ORDER OF MAGNITUDE: on run21
#: the shipped layout is 1,464,279 shapes and an unfaithful reference came back at
#: a fraction of that while a faithful one must carry all the standard cells.
_MIN_REFERENCE_SHAPE_FRACTION = 0.5


def reference_is_faithful(stdout: str) -> Tuple[bool, str, Dict[str, int]]:
    """(is it plausible, why not, the census).

    A reference missing a library is the failure mode this program must never
    report as a design finding, and it cannot be seen in the difference counts.
    So the two layouts' own censuses are compared, and an implausibly thin
    reference is REFUSED rather than trusted.
    """
    m = _CENSUS_RE.search(stdout)
    if not m:
        return False, "the comparison printed no census, so the reference could not be checked for faithfulness", {}
    stats = {"shipped_cells": int(m.group(1)), "shipped_shapes": int(m.group(2)),
             "reference_cells": int(m.group(3)), "reference_shapes": int(m.group(4))}
    if stats["shipped_shapes"] <= 0:
        return False, "the shipped layout reports zero shapes", stats
    frac = stats["reference_shapes"] / stats["shipped_shapes"]
    if frac < _MIN_REFERENCE_SHAPE_FRACTION:
        return False, (
            f"the reference carries {stats['reference_shapes']} shape(s) in "
            f"{stats['reference_cells']} cell(s) against the shipped layout's "
            f"{stats['shipped_shapes']} in {stats['shipped_cells']} "
            f"({frac:.1%}) — a pre-finishing reference is smaller, but not by that "
            f"much, so a library did not resolve and any difference count here "
            f"would be about THIS PROGRAM and not about the design"), stats
    return True, "", stats


def parse_layers(stdout: str) -> Tuple[Dict[Tuple[int, int], int], bool]:
    """({(layer, datatype): differing-polygon count}, the script ran to the end).

    The DONE marker is required: a truncated transcript is not a clean zero, which
    is the whole failure mode this module exists to avoid reproducing.
    """
    counts: Dict[Tuple[int, int], int] = {}
    for m in re.finditer(r"^XOR_LAYER (\d+)/(\d+) (\d+)$", stdout, re.M):
        counts[(int(m.group(1)), int(m.group(2)))] = int(m.group(3))
    return counts, bool(re.search(r"^XOR_DONE$", stdout, re.M))


def partition(counts: Dict[Tuple[int, int], int],
              fill_pairs: Set[Tuple[int, Optional[int]]],
              seal_pairs: Set[Tuple[int, int]]
              ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    """(design rows, finishing rows, design total). Only DIFFERING layers appear."""
    design: List[Dict[str, Any]] = []
    finishing: List[Dict[str, Any]] = []
    total = 0
    for (lay, dt), n in sorted(counts.items()):
        if n == 0:
            continue
        row = {"layer": lay, "datatype": dt, "differences": n}
        if (lay, dt) in seal_pairs:
            finishing.append({**row, "declared_by": "seal_ring"})
        elif (lay, dt) in fill_pairs or (lay, None) in fill_pairs:
            finishing.append({**row, "declared_by": "density_fill"})
        else:
            design.append(row)
            total += n
    return design, finishing, total


def reanchored(project: Path, recorded: str) -> str:
    """A path the run recorded, re-anchored to THIS project directory.

    The run records ABSOLUTE HOST paths for the artefacts it produced itself
    (`technology_units.json.tech_lef` is the legalized tlef in the run's own pnr
    dir). Read on a COPY of that run those paths point at the ORIGINAL root --
    another lane's directory, which this program must not read, and which on the
    dispatcher host may not exist at all. Re-anchoring is by the LONGEST TAIL THAT
    EXISTS under `project`: existence-tested, so it cannot silently invent a file,
    and convention-free, so it does not care that a copy is named `run21x` while
    the original was `run21`. A path that already resolves is returned untouched,
    and one with no existing tail is returned unchanged so the caller's own
    open-failure names the real path.
    """
    if not recorded or Path(recorded).exists():
        return recorded
    parts = PurePosixPath(recorded).parts
    for i in range(1, len(parts)):
        cand = project.joinpath(*parts[i:])
        if cand.exists():
            return str(cand)
    return recorded


#: The stream-out's OWN record of what it read, written beside the GDS it
#: streamed and keyed on that output's stem (`_stream_input_record`). The
#: re-stream reads it and nothing else: an earlier revision assembled the LEF
#: set from `technology_units.json`, the pad-ring IO record and a registry glob,
#: which is empty for every design without an IO ring -- MEASURED on fxspm1's
#: spm integration run, step 37.3 NOT_MEASURED for a GDS the flow had streamed.
STREAM_INPUTS_REL_FMT = "phase3/stage3/pnr/{stem}.stream_inputs.json"


def _inputs_hold_the_recorded_bytes(runner, pairs: List[Tuple[str, str]],
                                    timeout: int) -> Tuple[bool, str]:
    """Does each `(tool-side path, sha256)` hold those bytes WHERE THE TOOL RUNS?

    Asked in the runner's own environment -- a PDK file inside the container has
    no host copy -- through `sha256sum -c --status`, whose EXIT CODE is the
    answer; nothing is read out of its text. On a failure each input is asked
    alone, so the refusal names the one that moved.
    """
    def check(some: List[Tuple[str, str]]) -> int:
        sums = "".join(f"{sha}  {path}\n" for path, sha in some)
        rc, _so, _se = runner.run_argv(
            ["bash", "-c", 'printf "%s" "$VIBEIC_XOR_SUMS" | sha256sum -c --status -'],
            {"VIBEIC_XOR_SUMS": sums}, timeout=timeout)
        return rc
    if check(pairs) == 0:
        return True, ""
    for path, sha in pairs:
        if check([(path, sha)]) != 0:
            return False, (f"{path} does not hold the bytes the stream-out read "
                           f"(recorded sha256 {sha[:16]}...)")
    return False, ("the recorded inputs do not verify as a set although each "
                   "verifies alone")


def _def_design_name(def_file: Optional[Path]) -> str:
    """The DESIGN statement of a DEF — the name the runner writes the boundary under.

    Read from the DEF itself rather than inferred from its filename, because those
    are different strings on every pad-ring run and on every run whose DEF is
    `routed.def`: that mismatch is the defect this resolver exists to close.
    """
    if def_file is None:
        return ""
    try:
        with def_file.open("r", errors="replace") as fh:
            for line in fh:
                text = line.strip()
                if text.startswith("DESIGN "):
                    parts = text.split()
                    if len(parts) >= 2:
                        return parts[1].strip().rstrip(";").strip()
                if text.startswith("COMPONENTS "):
                    break            # DESIGN precedes COMPONENTS in a valid DEF
    except OSError:
        return ""
    return ""


def _boundary_is_this_runs(project: Path, kept: Path,
                           dfile: Optional[Path]) -> Tuple[bool, str]:
    """Is this retained boundary provably the one THIS run streamed?

    The runner writes a receipt beside it carrying the sha256 it computed, the size,
    and the DEF it streamed from with that DEF's sha256. A boundary whose receipt is
    absent, unreadable, or disagrees with the bytes now on disk is not evidence about
    this run; neither is one streamed from a DEF that is no longer the DEF being
    compared. Refusing is cheap -- the caller re-streams, which is what it did before
    any of this existed.
    """
    receipt = kept.with_suffix(".gds.receipt.json")
    try:
        rec = json.loads(receipt.read_text(errors="replace"))
    except (OSError, ValueError):
        return False, (f"no readable receipt at "
                       f"{receipt.relative_to(project).as_posix()}")
    if not isinstance(rec, dict):
        return False, "the receipt is not an object"
    live = _sha256(kept)
    if str(rec.get("sha256") or "") != live:
        return False, ("the receipt describes different bytes "
                       "(sha256 mismatch), so the file has been replaced since")
    if dfile is not None and rec.get("def_sha256"):
        if str(rec["def_sha256"]) != _sha256(dfile):
            return False, (f"it was streamed from a different "
                           f"{rec.get('streamed_from_def') or 'DEF'} than the one "
                           f"being compared")
    return True, ""


def resolve_reference(project: Path, top: str,
                     dfile: Optional[Path] = None) -> Tuple[Optional[Path], str,
                                                       Dict[str, Any]]:
    """(retained pre-finishing GDS, "retained"|"restreamed", provenance).

    PREFERRED: the boundary artefact the gds step retains. Then the comparison is
    against bytes THIS RUN wrote before finishing touched anything, and the
    expectation on design layers is exactly 0.

    FALLBACK, for runs that predate the artefact (run21 and earlier): re-stream
    through the run's OWN recipe. A re-streamed reference can differ by stream-out
    nondeterminism, so a nonzero there must be attributed per layer before anyone
    calls it a fidelity finding -- which is why the receipt says WHICH reference it
    used and never lets the two be confused.
    """
    # NAMED BY THE DEF'S OWN DESIGN, AND PROVEN TO BE THIS RUN'S. R-0915-148.
    #
    # My first cut resolved the exact `{top}` spelling and then GLOBBED
    # `*.prefinish.gds`. The glob fixed the name mismatch and opened a worse hole,
    # which a pre-landing review caught: nothing tied the single match to THIS run.
    # Only the KLayout stream-out branch retains a boundary; the Magic branch
    # retained nothing and deleted nothing, and nothing in the plugin ever unlinked
    # one. So a boundary left by an earlier KLayout invocation would be picked up by
    # a later Magic run and compared under "design-layer differences expected to be
    # exactly 0" -- turning any routing change between the two runs into a design
    # FAIL about the wrong layout. Before my commit the stem lookup simply MISSED and
    # the checker re-streamed, so the stale comparison would have been NEW.
    #
    # Two things close it. The name is DERIVED, not guessed: the runner names the
    # file after the DEF's own DESIGN statement, so this reads that statement. And
    # the artefact must be PROVABLE: the runner records a receipt beside it (sha256,
    # size, the DEF it was streamed from and that DEF's sha), and a boundary whose
    # receipt is absent, unreadable, or disagrees with the bytes on disk is NOT this
    # run's. An unprovable boundary re-streams, exactly as an absent one does --
    # never `retained`.
    design = _def_design_name(dfile) if dfile is not None else ""
    candidates = []
    if design:
        candidates.append(PREFINISH_REL_FMT.format(top=design))
    if top and top != design:
        candidates.append(PREFINISH_REL_FMT.format(top=top))
    kept, rel = None, ""
    for cand in candidates:
        if (project / cand).is_file():
            kept, rel = project / cand, cand
            break
    if kept is not None:
        ok, why = _boundary_is_this_runs(project, kept, dfile)
        if not ok:
            return None, "unprovable", {
                "kind": "unprovable",
                "path": rel,
                "note": (f"a retained finishing boundary is present but cannot be "
                         f"proven to belong to this run ({why}), so it is NOT used "
                         f"as the reference; the comparison re-streams the current "
                         f"DEF instead"),
            }
    if kept is not None and kept.is_file():
        return kept, "retained", {
            "kind": "retained", "path": rel, "sha256": _sha256(kept),
            "note": ("the stream-out the gds step kept BEFORE fill, seal ring and "
                     "snap; design-layer differences are expected to be exactly 0"),
        }
    return None, "restreamed", {
        "kind": "restreamed",
        "note": (f"{rel} is absent, so the reference was re-streamed through the "
                 f"run's own recorded stream-out (engine, recipe and inputs under "
                 f"`stream_inputs`); stream-out nondeterminism can "
                 f"move a count here, so any nonzero design-layer difference must "
                 f"be attributed per layer before it is read as a fidelity finding"),
    }


def stream_reference(runner, scratch: Path, dfile: Path, out: Path, timeout: int,
                     *, stem: Optional[str] = None) -> Tuple[int, str, str]:
    """Stream the routed DEF to GDS: the PRE-FINISHING reference.

    From the run's own record of its stream-out (`STREAM_INPUTS_REL_FMT`, keyed
    on `stem`, the shipped GDS's): the same engine and recipe, and every input
    it names, each proven to hold the bytes it held then. Any gap is a refusal
    that names it -- rc 127, never an assembled substitute, because a reference
    missing one library produces a confident wrong answer about the design.
    """
    project = scratch.parent
    rec_rel = STREAM_INPUTS_REL_FMT.format(stem=stem or dfile.stem)
    rec, why = _sir.read(project / rec_rel)
    if rec is None:
        if not (project / STREAMOUT_SCRIPT_REL).is_file():
            return 127, "", (f"{rec_rel} is {why} and {STREAMOUT_SCRIPT_REL} is "
                             f"absent, so the run's own stream-out recipe cannot "
                             f"be reused and this program will not substitute "
                             f"one of its own")
        return 127, "", (
            f"the run records no stream-out input set: {rec_rel} is {why}. The "
            f"stream-out step writes it beside the GDS it streamed (engine, "
            f"recipe, LEFs, cell/macro GDS, layer map, each with its sha256), "
            f"and this program will not assemble one from other records -- a "
            f"reference missing one library measures the wrong thing")
    recipe_rel = (rec.get("recipe") or {}).get("project_rel")
    own = project / recipe_rel if recipe_rel else None
    if own is None or not own.is_file():
        return 127, "", (f"{recipe_rel or 'the recorded recipe'} is absent, so "
                         f"the run's own stream-out recipe cannot be reused and "
                         f"this program will not substitute one of its own")
    if _sha256(own) != (rec.get("recipe") or {}).get("sha256"):
        return 127, "", (f"{recipe_rel} changed since the stream-out ran "
                         f"({rec_rel} records a different sha256), so running it "
                         f"again would not reproduce that stream")
    def_rec = rec.get("def") or {}
    if def_rec.get("sha256") != _sha256(dfile):
        return 127, "", (f"the recorded stream-out read "
                         f"{def_rec.get('project_rel')} (sha256 "
                         f"{str(def_rec.get('sha256'))[:16]}...), not the DEF this "
                         f"check compares ({dfile.name}), so the reference would "
                         f"be of a different layout")
    unhashed = [e["path"] for e in _sir.all_entries(rec) if not e.get("sha256")]
    if unhashed:
        return 127, "", (f"{rec_rel} carries no sha256 for {len(unhashed)} "
                         f"input(s) (first: {unhashed[0]}), so a re-stream cannot "
                         f"prove it reads what the stream-out read")

    def resolve(entry: Dict[str, Any]) -> str:
        """The tool-side path: a project input re-anchored to THIS project
        (a copy of a run), anything else exactly as the stream-out saw it."""
        rel = entry.get("project_rel")
        if rel and (project / rel).exists():
            return str(runner.cpath(project / rel))
        return str(entry["path"])

    ok, why = _inputs_hold_the_recorded_bytes(
        runner, [(resolve(e), e["sha256"]) for e in _sir.all_entries(rec)],
        timeout)
    if not ok:
        return 127, "", f"{why}, so a re-stream would not read what the stream-out read"
    env: Dict[str, Any] = dict(_sir.iter_env(rec, resolve))
    env["TOP"] = rec.get("top") or dfile.stem
    if rec["engine"] == "klayout":
        env.update({"DEF": str(dfile), "GDS_OUT": str(out)})
        return runner.run(own, env, path_keys=("DEF", "GDS_OUT"), timeout=timeout)
    env.update({"DEF": runner.cpath(dfile), "GDS_OUT": runner.cpath(out)})
    return runner.run_argv(["magic", "-dnull", "-noconsole", "-rcfile",
                            resolve(rec["rcfile"]), runner.cpath(own)],
                           env, timeout=timeout)



# ══════════════════════════════════════════════════════════════════════
# THE JUDGE: read the run's own receipt, decide the step, measure nothing
# ══════════════════════════════════════════════════════════════════════
#: Typed so the flow's own taxonomy can classify the non-verdict instead of
#: guessing at prose. `_flow_reason_taxonomy.CAPABILITY_ABSENT` is the class for
#: a tool this checkout cannot reach; INPUT_ABSENT-shaped cases (no receipt at
#: all) are `ASKED_BEFORE_PRODUCER`, which is what an audit clause asked before
#: its producer ran actually is.
_JUDGE_CAPABILITY_ABSENT = "CAPABILITY_ABSENT"
_JUDGE_ASKED_BEFORE_PRODUCER = "ASKED_BEFORE_PRODUCER"


#: Step 37's LibreLane promotion record (librelane_step37.run).
LIBRELANE_PROMOTION_REL = "phase3/librelane/37-promotion.json"


def librelane_finishing_receipt(project: Path, live_sha256: str
                                ) -> Optional[Dict[str, Any]]:
    """The 37.3 receipt for a GDS step 37's LibreLane chain promoted, or None.

    None unless ``37-promotion.json`` names these exact bytes
    (``canonical_sha256`` == the shipped GDS's sha256). Then the promoted arm's
    judged ``Vibeic.FinishingXOR`` record decides: PASS -> 0 design-layer
    differences; a defect -> each design pair it names, from the State's
    per-pair metrics; a record that cannot be read or was not measured ->
    NOT_DETERMINED with its reason (never a zero).
    """
    promo_path = project / LIBRELANE_PROMOTION_REL
    if not promo_path.is_file():
        return None
    try:
        promo = json.loads(promo_path.read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(promo, dict) or promo.get("canonical_sha256") != live_sha256:
        return None
    arm = promo.get("selection")
    rel = f"phase3/librelane/37.3-{arm}-finishing-xor.json"
    base = {"reference": {"kind": "librelane_stream", "arm": arm,
                          "promotion": LIBRELANE_PROMOTION_REL,
                          "finishing_record": rel}}
    try:
        record = json.loads((project / rel).read_text(errors="replace"))
    except (OSError, ValueError) as exc:
        return {"verdict": "NOT_DETERMINED", "rc": 2, "report": base,
                "reason": f"step 37 promoted a LibreLane stream but its finishing "
                          f"record {rel} is unreadable ({type(exc).__name__})"}
    rows = record.get("metrics") or {}
    defect = rows.get("vibeic__finishing_xor__defect__count") or {}
    if record.get("verdict") not in ("PASS", "FAIL") or \
            defect.get("status") not in ("MEASURED", "FAIL"):
        return {"verdict": "NOT_DETERMINED", "rc": 2, "report": base,
                "reason": f"{rel}: the finishing XOR was not measured "
                          f"({'; '.join(record.get('reasons') or []) or record.get('verdict')})"}
    state = {}
    folder = defect.get("folder")
    try:
        state = json.loads((Path(folder) / "state_out.json").read_text()).get("metrics") or {}
    except (OSError, ValueError, TypeError):
        state = {}
    prefix = "vibeic__finishing_xor__defect__count__pair:"
    diffs = []
    for key, value in sorted(state.items()):
        if key.startswith(prefix) and isinstance(value, int) and value > 0:
            layer, _, datatype = key[len(prefix):].partition("_")
            diffs.append({"layer": int(layer), "datatype": int(datatype),
                          "differences": value, "declared_by": "Vibeic.FinishingXOR"})
    count = defect.get("value")
    if count and not diffs:
        diffs = [{"layer": None, "datatype": None, "differences": count,
                  "declared_by": "Vibeic.FinishingXOR (no per-pair metric)"}]
    report = dict(base, design_layer_differences=diffs,
                  design__xor_difference__count=count,
                  layers_compared=state.get("vibeic__finishing_xor__design_pair__count"),
                  finishing_record_sha256=_sha256(project / rel))
    if count == 0:
        return {"verdict": "PASS", "rc": 0, "report": report,
                "reason": f"step 37's LibreLane finishing XOR on the promoted "
                          f"{arm} stream: 0 design geometry removed, covered or "
                          f"touched ({rel})"}
    return {"verdict": "FAIL", "rc": 1, "report": report,
            "reason": f"step 37's LibreLane finishing XOR on the promoted {arm} "
                      f"stream counted {count} defect(s) ({rel})"}


def judge_receipt(project: Path, rel: str) -> Tuple[int, str, Dict[str, Any]]:
    """Decide step 37.3 from the receipt the PRODUCER wrote. Returns (rc, line, doc).

    WHY A JUDGE AND NOT A SECOND MEASUREMENT, measured on the SLT53D S arm. The
    flow clause used to be `gds_xor_check . --json reports/phase3/gds_xor.json`,
    so the completion audit RE-RAN the comparison. On that arm the producer's
    receipt said PASS, rc 0, 0 design-layer differences across 46 layers, runner
    {"kind": "container", "detail": "real-ic-arm-eda:klayout"} -- and the audit's
    own invocation of the same program, seconds later on the same host, could not
    reach KLayout (the static clause carries no `--container`) and exited
    NOT_DETERMINED. The audit published the invocation that did not measure, and
    every one of the five subjects read FAIL.

    R-0915-126 already names the rule this broke: PRODUCER WRITES, GATE READS.
    The receipt redirect exists precisely so a gate clause can never overwrite a
    producer document -- and a clause that re-measures is the trap the redirect
    was built for, because the redirect protects the BYTES and says nothing about
    which of the two answers gets published.

    THE MAPPING, and every branch is one a reader can check against the receipt:
      * no receipt                  -> rc 2, ASKED_BEFORE_PRODUCER
      * unreadable / not this gate  -> rc 2, EXECUTION_ERROR-shaped, named
      * verdict NOT_DETERMINED      -> rc 2, carrying the receipt's OWN reason
      * design-layer differences    -> rc 1  (a measured defect, and the answer
                                       to dimension 2's question about this step)
      * zero differences            -> rc 0
    Nothing here opens a layout, launches a tool or writes a byte.
    """
    path = project / rel
    if not path.is_file():
        return 2, (
            f"NOT_MEASURED [{_JUDGE_ASKED_BEFORE_PRODUCER}]: {rel} does not "
            f"exist, so this step's own producer has not run yet; an absent "
            f"receipt is a question nobody asked, not a clean comparison"), {}
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError) as exc:
        return 2, (
            f"NOT_MEASURED [EXECUTION_ERROR]: {rel} is not readable JSON "
            f"({type(exc).__name__}: {exc}), so it states nothing this step can "
            f"be judged on"), {}
    if not isinstance(doc, dict) or doc.get("gate") != GATE:
        return 2, (
            f"NOT_MEASURED [EXECUTION_ERROR]: {rel} is not a {GATE} receipt "
            f"(gate={doc.get('gate') if isinstance(doc, dict) else type(doc).__name__})"
        ), (doc if isinstance(doc, dict) else {})

    verdict = str(doc.get("verdict") or "")
    diffs = doc.get("design_layer_differences")
    count = doc.get("design__xor_difference__count")
    reason = str(doc.get("reason") or "no reason recorded")
    layers = doc.get("layers_compared")

    if verdict == "NOT_DETERMINED" or not isinstance(diffs, list):
        # THE RECEIPT'S OWN REASON, carried verbatim. The producer knows why it
        # could not measure; re-deriving that here is how two readers come to
        # disagree about one run.
        return 2, (
            f"NOT_MEASURED [{_JUDGE_CAPABILITY_ABSENT}]: the run's own receipt "
            f"{rel} records verdict NOT_DETERMINED — {reason}"), doc
    if diffs or (isinstance(count, int) and count > 0):
        named = ", ".join(
            f"{d.get('layer')}/{d.get('datatype')}={d.get('differences')}"
            for d in diffs[:6] if isinstance(d, dict))
        return 1, (
            f"FAIL: the run's own receipt {rel} records {len(diffs)} DESIGN "
            f"layer(s) differing between the shipped GDS and this run's "
            f"pre-finishing reference ({count} differing polygon(s): {named})"), doc
    return 0, (
        f"PASS: the run's own receipt {rel} records 0 design-layer difference(s) "
        f"across {layers} layer(s) compared — {reason}"), doc


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_dir")
    ap.add_argument("--json", default=None,
                    help=f"where to write the report (default {REPORT_REL})")
    ap.add_argument("--container", default=None)
    ap.add_argument("--check", metavar="RECEIPT", default=None,
                    help="JUDGE the receipt at RECEIPT (project-relative) "
                         "instead of performing the comparison: 0 design-layer "
                         "differences -> 0, a measured difference -> 1, a "
                         "NOT_DETERMINED or absent receipt -> 2. Reads only; "
                         "this is the form the flow's gate clause uses, so the "
                         "audit judges the producer's measurement rather than "
                         "re-running it with a resolver that may reach no tool.")
    ap.add_argument("--timeout", type=int, default=0,
                    help="0 (the default) means NO deadline: a 100 MB XOR is "
                         "working, not wedged, and the watchdog supervises "
                         "progress instead")
    args = ap.parse_args(argv)

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"[{GATE}] project dir not found: {project}", file=sys.stderr)
        return 1

    if args.check:
        rc, line, _doc = judge_receipt(project, args.check)
        # THE CLASSIFIED LINE FIRST, AT COLUMN 0, AND THAT IS NOT COSMETIC.
        # Every reader of a gate's non-verdict in this flow takes the FIRST line
        # of stdout -- `_p0_skip_reason_from_output` does, and so does the output
        # snippet the step row carries. Printing a decorative `=== gate ===`
        # banner first hands all of them the banner: MEASURED on a run21 copy,
        # the row read "gate program signalled VACUOUS_PASS (input not
        # applicable)" and the receipt's own reason ("no KLayout runner reaches
        # this project") reached nobody. The banner still prints, second, for a
        # human reading a terminal.
        print(line)
        print(f"=== {GATE} --check ({project.name}) ===")
        # step_metrics_adoption_check: step 37.3 declares this program, so the
        # verdict it just judged is emitted through the one metrics schema,
        # attributed to the clause that ran it (`--check <receipt>`). The
        # receipt IS the verdict document. Best-effort: cannot change rc.
        import step_metrics  # noqa: PLC0415
        step_metrics.emit_gate_outcome(
            "gds_xor_check", list(argv if argv is not None else sys.argv[1:]),
            rc, report=args.check)
        return rc
    out = Path(args.json) if args.json else (project / REPORT_REL)
    if not out.is_absolute():
        out = (Path.cwd() / out).resolve()

    # rc 2 FOR NOT_DETERMINED -- the not-checked convention every other gate in
    # this flow uses -- and rc 1 ONLY for a measured design-layer difference.
    #
    # THIS WAS rc 1 FOR BOTH, AND THE COST WAS MEASURED. An earlier draft exited 2
    # on a refusal, dimension 2 caught that ("step 37.3 gate CANNOT FAIL on
    # anything a project DID"), and the over-correction was to make every refusal
    # exit 1 -- which made "the tool was not reachable" indistinguishable from
    # "the design's geometry changed". On the SLT53D S arm that published a FAIL
    # over a PASS: the producer measured 0 differences across 46 layers and the
    # audit's own re-invocation, which could not reach KLayout, exited 1 and the
    # row read FAIL on all five subjects.
    #
    # The two questions are now separated where they belong. A REFUSAL is rc 2,
    # which the audit maps to a typed non-verdict, never a defect -- an unrun XOR
    # is not a zero and it is not a difference either. A MEASURED DIFFERENCE is
    # rc 1 and blocks. And dimension 2's question is answered by `--check`, the
    # judge the flow clause now names: hand it a receipt with one design-layer
    # difference and it exits 1 on a project that DID something.
    report: Dict[str, Any] = {"gate": GATE, "project": str(project),
                              "verdict": "NOT_DETERMINED", "rc": 2}
    shipped, dfile, att = shipped_and_source(project)
    report["attestation"] = att
    fill_pairs, seal_pairs, prov = declared_finishing_layers(project)
    report["declared_finishing_layers"] = prov

    def finish(verdict: str, rc: int, reason: str) -> int:
        report.update(verdict=verdict, rc=rc, reason=reason)
        # THE CLASS, STATED BY THE GATE THAT KNOWS IT. `_flow_reason_taxonomy
        # .report_reason_class` reads this field and `_p0_declared_reason_class`
        # prefers it over every prose recogniser -- "branch-owned evidence
        # outranks prose". Without it a refusal is typed by pattern-matching the
        # sentence, which is how "the tool was unreachable" and "this design has
        # no such thing" come to share a class.
        if verdict == "NOT_DETERMINED":
            report["reason_class"] = _JUDGE_CAPABILITY_ABSENT
        else:
            report.pop("reason_class", None)
        out.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(out, report)
        print(f"=== {GATE} ({project.name}) ===")
        print(f"  verdict: {verdict}  (rc={rc})")
        print(f"  {reason}")
        return rc

    # NAME BOTH PLACES THAT WERE LOOKED IN. A refusal that cites only the
    # attestation sends a reader to a file that a design off the chip pad-ring
    # path never had, and they would read "missing attestation" where the truth is
    # "no shipped GDS anywhere".
    if shipped is None or not shipped.is_file():
        return finish("NOT_DETERMINED", 2,
                      f"no shipped GDS exists: neither {ROUTE_EVIDENCE_REL} nor "
                      f"{CANONICAL_GDS_GLOB} names one on disk, so there is "
                      f"nothing to compare; this is an absent input, not a clean "
                      f"XOR")
    if dfile is None or not dfile.is_file():
        return finish("NOT_DETERMINED", 2,
                      f"no routed DEF exists: neither {ROUTE_EVIDENCE_REL} nor "
                      f"{CANONICAL_DEF_REL} names one on disk, so no "
                      f"pre-finishing reference can be streamed")

    # IDENTITY FIRST. The attestation's sha256 is what makes this the pair the run
    # shipped; a GDS that changed since is a different subject and is refused
    # rather than silently compared.
    live = _sha256(shipped)
    report["shipped_sha256_live"] = live
    recorded = att.get("gds_sha256_recorded")
    if isinstance(recorded, str) and recorded and recorded != live:
        return finish("NOT_DETERMINED", 2,
                      f"the shipped GDS changed since the run attested it "
                      f"({ROUTE_EVIDENCE_REL} records {recorded[:16]}..., on disk "
                      f"{live[:16]}...), so this run's own pairing no longer holds")

    # STEP 37 ON LIBRELANE (lane mig105). When the shipped bytes are exactly
    # the stream LibreLane's step-37 chain promoted, the finishing comparison
    # was already MEASURED inside that chain, by `Vibeic.FinishingXOR` on the
    # promoted arm's own three streams (StreamOut, SealRing, Filler). The
    # receipt is written from that judged record -- one instrument, one
    # answer -- and nothing is re-streamed. Any other bytes fall through to
    # the direct comparison below.
    ll = librelane_finishing_receipt(project, live)
    if ll is not None:
        report.update(ll["report"])
        return finish(ll["verdict"], ll["rc"], ll["reason"])

    try:
        import _klayout_launch as _kl
    except ImportError as exc:                             # pragma: no cover
        return finish("NOT_DETERMINED", 2, f"KLayout launcher unavailable: {exc}")
    # THE ONE RESOLVER, project included: with no explicit --container the run's
    # own `reports/container_image.json` names the container it used, so this
    # invocation and the runner's resolve the same environment instead of falling
    # through to a conventional default that mounts a different tree.
    runner = _kl.find_runner(args.container, project=project)
    if runner is None or not runner.covers(shipped):
        return finish("NOT_DETERMINED", 2,
                      "no KLayout runner reaches this project, so the comparison "
                      "was not performed; an unrun XOR is not a zero")
    report["runner"] = {"kind": runner.kind, "detail": runner.detail}

    timeout = args.timeout if args.timeout > 0 else 24 * 3600
    kept, kind, prov = resolve_reference(project, dfile.stem, dfile)
    report["reference"] = prov
    with tempfile.TemporaryDirectory(prefix="gds_xor_ref_",
                                     dir=str(project)) as scratch:
        if kept is not None:
            reference = kept
        else:
            reference = Path(scratch) / "pre_finishing.gds"
            rec_rel = STREAM_INPUTS_REL_FMT.format(stem=shipped.stem)
            rec, _why = _sir.read(project / rec_rel)
            report["reference"]["stream_inputs"] = (
                {"path": rec_rel, "engine": rec["engine"],
                 "inputs": len(_sir.all_entries(rec))} if rec else
                {"path": rec_rel, "absent": _why})
            rc, so, se = stream_reference(runner, Path(scratch), dfile,
                                          reference, timeout, stem=shipped.stem)
            if rc != 0 or not reference.is_file():
                return finish("NOT_DETERMINED", 2,
                              f"re-streaming the pre-finishing reference from "
                              f"{att.get('def')} failed (rc={rc}): "
                              f"{(se or so or '').strip()[:220]}")
            report["reference"]["streamed_from"] = att.get("def")
            # What the tool's own transcript says it resolved, beside the record
            # of what it was handed -- disclosure for a reader, not an input.
            _t_env, cited = restream_env_from_transcript(project)
            if cited:
                report["reference"]["stream_transcript"] = cited
        report["reference"]["bytes"] = reference.stat().st_size
        rc, so, se = run_xor(runner, Path(scratch), shipped, reference,
                             timeout)
        counts, done = parse_layers(so)
        ok, why, stats = reference_is_faithful(so)
        report["census"] = stats
        if done and not ok:
            return finish("NOT_DETERMINED", 2, f"refusing to compare: {why}")
        if rc != 0 or not done:
            return finish("NOT_DETERMINED", 2,
                          f"the XOR did not run to completion (rc={rc}, "
                          f"XOR_DONE={'yes' if done else 'no'}): "
                          f"{(se or so or '').strip()[:200]}")

    design, finishing, total = partition(counts, fill_pairs, seal_pairs)
    report["layers_compared"] = len(counts)
    report["design_layer_differences"] = design
    report["finishing_layer_differences"] = finishing
    report["design__xor_difference__count"] = total
    if total:
        named = ", ".join(f"{r['layer']}/{r['datatype']}={r['differences']}"
                          for r in design[:6])
        return finish("FAIL", 1,
                      f"the shipped GDS differs from the {kind} pre-finishing "
                      f"reference on {len(design)} DESIGN layer(s) "
                      f"({total} differing polygon(s): {named}) — finishing may add "
                      f"geometry on the layers it declares, and these are not "
                      f"those")
    return finish("PASS", 0,
                  f"the shipped GDS matches the {kind} pre-finishing reference "
                  f"on every design layer (0 differences across "
                  f"{len(counts)} layer(s) compared); "
                  f"{len(finishing)} finishing-declared layer(s) differ as "
                  f"expected and are listed separately")


if __name__ == "__main__":
    sys.exit(main())
