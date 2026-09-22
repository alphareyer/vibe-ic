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
#: The run's own retained stream-out recipe and its transcript. A fallback that
#: re-streams must use THE RUN'S recipe, not a hand-rolled read: that mistake
#: produced 776,403 differences across 29 design layers on run21 -- placement boxes
#: against real standard-cell geometry -- and it was mine, not the design's.
STREAMOUT_SCRIPT_REL = "phase3/stage3/pnr/stream_out.py"
STREAMOUT_LOG_REL = "phase3/stage3/pnr/stream_out.log"

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
    try:
        text = (project / STREAMOUT_LOG_REL).read_text(errors="replace")
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
        cited.append(f"{STREAMOUT_LOG_REL}: {key} = {env[key][:120]}")
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


def lef_set_from_the_run(project: Path) -> Dict[str, str]:
    """{"LEFS": ...} assembled from READINGS only, never a path convention.

    `step_gds` builds LEFS as `[tech_lef, cell_lef] + macro_lefs`. Each piece has a
    recorded or DECLARED source, and this takes them from there:
      * tech LEF   the run's own `reports/phase3/technology_units.json` -- and its
                   value is the LEGALIZED tlef in the pnr dir, which is better than
                   any declaration because the run patched it;
      * macro LEFs the run's own `reports/phase3/io_pad_chip_top.json`
                   `io_library_lefs` (15 views on run21);
      * cell LEF   `pdk_registry.json`'s DECLARED `cell_lef_glob`, resolved against
                   the PDK volume the run recorded, through the same reader 15.5ic
                   and 37.5ic use.
    Returns {} when any of the three is unavailable, and the caller then REFUSES --
    a re-stream missing a library is the defect that produced 776,403 phantom
    differences, so it must never be attempted half-resolved.
    """
    tech = ((_json(project / "reports/phase3/technology_units.json") or {})
            .get("tech_lef"))
    io_rec = _json(project / "reports/phase3/io_pad_chip_top.json") or {}
    macros = [x for x in (io_rec.get("io_library_lefs") or []) if isinstance(x, str)]
    if not isinstance(tech, str) or not tech or not macros:
        return {}
    try:
        import _pdk_layer_authority as authority
        reader, _why = authority.reader_the_run_recorded(project)
        pdk = ((_json(project / "reports/phase3/general_precheck.json") or {})
               .get("technology") or {}).get("pdk")
        volume, _how, _tried = authority.resolve_volume(pdk, reader=reader)
        if volume is None:
            return {}
        entry = next((e for e in (json.loads(
            (Path(__file__).resolve().parent / "pdk_registry.json")
            .read_text()).get("pdks") or []) if e.get("name") == pdk), None)
        glob_rel = (entry or {}).get("cell_lef_glob")
        if not isinstance(glob_rel, str) or not glob_rel:
            return {}
        cells = authority._query(volume / glob_rel.rsplit("/", 1)[0], "glob",
                                 glob_rel.rsplit("/", 1)[1], reader=reader)
    except Exception:                                      # pragma: no cover
        return {}
    cells = [c for c in (cells or []) if isinstance(c, str)]
    if not cells:
        return {}
    # Re-anchor the RUN-PRODUCED entries (tech LEF, the IO views the run resolved)
    # to this project; the PDK cell LEFs are already container-side and are left be.
    tech = reanchored(project, tech)
    macros = [reanchored(project, m) for m in macros]
    return {"LEFS": ";".join([tech] + sorted(cells) + macros)}


def resolve_reference(project: Path, top: str) -> Tuple[Optional[Path], str,
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
    rel = PREFINISH_REL_FMT.format(top=top)
    kept = project / rel
    if kept.is_file():
        return kept, "retained", {
            "kind": "retained", "path": rel, "sha256": _sha256(kept),
            "note": ("the stream-out the gds step kept BEFORE fill, seal ring and "
                     "snap; design-layer differences are expected to be exactly 0"),
        }
    return None, "restreamed", {
        "kind": "restreamed",
        "note": (f"{rel} is absent, so the reference was re-streamed through the "
                 f"run's own {STREAMOUT_SCRIPT_REL}; stream-out nondeterminism can "
                 f"move a count here, so any nonzero design-layer difference must "
                 f"be attributed per layer before it is read as a fidelity finding"),
    }


def stream_reference(runner, scratch: Path, dfile: Path, out: Path, timeout: int
                     ) -> Tuple[int, str, str]:
    """Stream the routed DEF to GDS: the PRE-FINISHING reference.

    Same engine the run used for the shipped GDS (`streamout_engine: klayout` on
    run21), so the comparison is not confounded by a second writer's conventions.
    """
    project = scratch.parent
    own = project / STREAMOUT_SCRIPT_REL
    if not own.is_file():
        return 127, "", (f"{STREAMOUT_SCRIPT_REL} is absent, so the run's own "
                         f"stream-out recipe cannot be reused and this program "
                         f"will not substitute one of its own")
    env, _cited = restream_env_from_transcript(project)
    env.update(lef_set_from_the_run(project))
    if "LEFS" not in env:
        return 127, "", (
            "the run records no LEF set for its stream-out (no tech LEF in "
            "reports/phase3/technology_units.json, or no cell LEF resolvable from "
            "pdk_registry.json's declared cell_lef_glob), and inventing one is how "
            "a reference ends up measuring the wrong thing")
    if "CELL_GDS" not in env:
        return 127, "", (f"{STREAMOUT_LOG_REL} names no CELL_GDS substitution, so a "
                         f"re-stream would resolve no standard-cell geometry and "
                         f"every layer would differ for that reason alone")
    top = dfile.stem
    env.update({"TOP": top, "DEF": str(dfile), "GDS_OUT": str(out)})
    # Only the PROJECT-side paths are translated. LEFDEF_MAP / CELL_GDS /
    # MACRO_GDS are already container-side PDK paths as the transcript recorded
    # them, and translating them again would corrupt them.
    # LEFS is a ";"-joined MIXTURE: container-side PDK paths plus host-side paths
    # to LEFs this run produced. `path_keys` translates a key whose whole value is
    # one path, so the mixed key is translated entry by entry here -- only the
    # entries that actually lie inside the project, leaving the PDK's own paths
    # exactly as the transcript recorded them.
    translated = []
    for one in env["LEFS"].split(";"):
        cand = Path(one)
        if cand.is_file() and runner.covers(cand):
            translated.append(str(runner.cpath(cand)))
        else:
            translated.append(one)
    env["LEFS"] = ";".join(translated)
    return runner.run(own, env, path_keys=("DEF", "GDS_OUT"), timeout=timeout)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_dir")
    ap.add_argument("--json", default=None,
                    help=f"where to write the report (default {REPORT_REL})")
    ap.add_argument("--container", default=None)
    ap.add_argument("--timeout", type=int, default=0,
                    help="0 (the default) means NO deadline: a 100 MB XOR is "
                         "working, not wedged, and the watchdog supervises "
                         "progress instead")
    args = ap.parse_args(argv)

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"[{GATE}] project dir not found: {project}", file=sys.stderr)
        return 1
    out = Path(args.json) if args.json else (project / REPORT_REL)
    if not out.is_absolute():
        out = (Path.cwd() / out).resolve()

    # rc 1, NOT 2, AND THE DIFFERENCE IS THE WHOLE POINT. This flow maps rc 2
    # onto VACUOUS_PASS -- a NON-FAIL tier -- so a refusal that exited 2 let the
    # step pass on a project where the comparison never happened. MEASURED by the
    # matrix's own dimension 2, which is exactly the question it asks: "step 37.3
    # gate CANNOT FAIL on anything a project DID: all 1 blocking clause(s)
    # reached a non-FAIL tier on a deliberately-broken project". An unrun XOR is
    # not a zero and an unverifiable claim is not a pass, so the refusal BLOCKS;
    # the `verdict` field, which `signoff_metrics_aggregate` reads, is what keeps
    # NOT_DETERMINED distinguishable from a measured FAIL.
    report: Dict[str, Any] = {"gate": GATE, "project": str(project),
                              "verdict": "NOT_DETERMINED", "rc": 1}
    shipped, dfile, att = shipped_and_source(project)
    report["attestation"] = att
    fill_pairs, seal_pairs, prov = declared_finishing_layers(project)
    report["declared_finishing_layers"] = prov

    def finish(verdict: str, rc: int, reason: str) -> int:
        report.update(verdict=verdict, rc=rc, reason=reason)
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
        return finish("NOT_DETERMINED", 1,
                      f"no shipped GDS exists: neither {ROUTE_EVIDENCE_REL} nor "
                      f"{CANONICAL_GDS_GLOB} names one on disk, so there is "
                      f"nothing to compare; this is an absent input, not a clean "
                      f"XOR")
    if dfile is None or not dfile.is_file():
        return finish("NOT_DETERMINED", 1,
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
        return finish("NOT_DETERMINED", 1,
                      f"the shipped GDS changed since the run attested it "
                      f"({ROUTE_EVIDENCE_REL} records {recorded[:16]}..., on disk "
                      f"{live[:16]}...), so this run's own pairing no longer holds")

    try:
        import _klayout_launch as _kl
    except ImportError as exc:                             # pragma: no cover
        return finish("NOT_DETERMINED", 1, f"KLayout launcher unavailable: {exc}")
    runner = _kl.find_runner(args.container)
    if runner is None or not runner.covers(shipped):
        return finish("NOT_DETERMINED", 1,
                      "no KLayout runner reaches this project, so the comparison "
                      "was not performed; an unrun XOR is not a zero")
    report["runner"] = {"kind": runner.kind, "detail": runner.detail}

    timeout = args.timeout if args.timeout > 0 else 24 * 3600
    kept, kind, prov = resolve_reference(project, dfile.stem)
    report["reference"] = prov
    with tempfile.TemporaryDirectory(prefix="gds_xor_ref_",
                                     dir=str(project)) as scratch:
        if kept is not None:
            reference = kept
        else:
            reference = Path(scratch) / "pre_finishing.gds"
            rc, so, se = stream_reference(runner, Path(scratch), dfile,
                                          reference, timeout)
            if rc != 0 or not reference.is_file():
                return finish("NOT_DETERMINED", 1,
                              f"re-streaming the pre-finishing reference from "
                              f"{att.get('def')} failed (rc={rc}): "
                              f"{(se or so or '').strip()[:220]}")
            report["reference"]["streamed_from"] = att.get("def")
        report["reference"]["bytes"] = reference.stat().st_size
        rc, so, se = run_xor(runner, Path(scratch), shipped, reference,
                             timeout)
        counts, done = parse_layers(so)
        ok, why, stats = reference_is_faithful(so)
        report["census"] = stats
        if done and not ok:
            return finish("NOT_DETERMINED", 1, f"refusing to compare: {why}")
        if rc != 0 or not done:
            return finish("NOT_DETERMINED", 1,
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
