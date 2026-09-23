"""R-0915-129 metric 2 — stream-out / finishing fidelity is MEASURED, not assumed.

Every case here was written from a defect this program actually committed while it
was being brought up on a copy of run21, and each pins the reading that defect
made wrong. Nothing mocks KLayout: the cases drive the pure readers and the
partition, which is where every wrong answer came from.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gds_xor_check as g                                    # noqa: E402


def _write(project: Path, rel: str, payload) -> None:
    p = project / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2) + "\n")


def _run21_fill_records(project: Path, *, whole=True, datatype=True) -> None:
    """The shape run21's own receipts have, with the two gaps switchable."""
    fill = {"fill": {"owned_layers": [34, 36, 42, 46, 81]}}
    if whole:
        fill["fill"]["probe"] = {
            "layers_the_whole_generator_writes": [22, 30, 34, 36, 42, 46, 81],
            "layers_per_pass": {"fill_comp.rb": [22], "fill_poly2.rb": [30],
                                "fill_metal.rb": [34, 36, 42, 46, 81]},
        }
    _write(project, g.FILL_REL, fill)
    if datatype:
        _write(project, g.CMP_FILL_REL,
               {"layers": [{"name": "metal1", "fill_datatype": 4},
                           {"name": "metal2", "fill_datatype": 4}]})
    _write(project, g.FINISHING_REL,
           {"seal_ring": {"ring_check": {"added_layers": ["22/0", "33/0", "34/0"]}}})


# --- the declaration: the whole generator, not one pass of it -----------------

def test_the_fill_exemption_covers_every_pass_the_generator_declares(tmp_path):
    """MEASURED DEFECT: reading `fill.owned_layers` (the METAL pass) left 22/4 and
    30/4 unattributed, and the program called 85,581 fill polygons a DESIGN
    fidelity finding. The receipt names all three passes in the same document."""
    _run21_fill_records(tmp_path)
    fill_pairs, _seal, prov = g.declared_finishing_layers(tmp_path)
    assert (22, 4) in fill_pairs and (30, 4) in fill_pairs
    assert prov["fill"]["field"] == "fill.probe.layers_the_whole_generator_writes"
    assert prov["fill"]["layers"] == [22, 30, 34, 36, 42, 46, 81]


def test_the_exemption_is_datatype_precise_so_the_base_layer_stays_checked(tmp_path):
    """The fill lands on the datatype its emitter DECLARES (4). Exempting layer 22
    outright would also excuse 22/0 -- a real design layer. This is strictly
    TIGHTER than the layer-wide set it replaced."""
    _run21_fill_records(tmp_path)
    fill_pairs, _seal, prov = g.declared_finishing_layers(tmp_path)
    assert (22, None) not in fill_pairs
    assert (22, 0) not in fill_pairs
    assert prov["fill"]["granularity"] == "layer/datatype"
    design, finishing, total = g.partition({(22, 0): 7, (22, 4): 75175},
                                           fill_pairs, _seal)
    # 22/0 is a declared SEAL layer here, so it lands in finishing for THAT
    # reason -- named, not silently absorbed by the fill rule.
    assert [r["declared_by"] for r in finishing] == ["seal_ring", "density_fill"]
    assert total == 0


def test_with_no_declared_datatype_the_exemption_stays_layer_wide(tmp_path):
    """A run whose emitter names no datatype must not be tightened by guesswork;
    the provenance says which granularity was used."""
    _run21_fill_records(tmp_path, datatype=False)
    fill_pairs, _seal, prov = g.declared_finishing_layers(tmp_path)
    assert (34, None) in fill_pairs
    assert prov["fill"]["granularity"] == "layer-wide"


def test_an_undeclared_layer_is_a_design_layer(tmp_path):
    """THE TEETH. A difference on a layer no finishing step claims is a finding,
    and this is the case that must go red if the exemption is ever widened."""
    _run21_fill_records(tmp_path)
    fill_pairs, seal, _prov = g.declared_finishing_layers(tmp_path)
    design, _fin, total = g.partition({(32, 0): 1}, fill_pairs, seal)
    assert total == 1
    assert design == [{"layer": 32, "datatype": 0, "differences": 1}]


def test_zero_difference_layers_are_not_rows(tmp_path):
    _run21_fill_records(tmp_path)
    fill_pairs, seal, _prov = g.declared_finishing_layers(tmp_path)
    design, finishing, total = g.partition({(32, 0): 0, (34, 4): 0},
                                           fill_pairs, seal)
    assert (design, finishing, total) == ([], [], 0)


# --- the faithfulness guard: a thin reference is refused, never compared ------

_CENSUS = ("XOR_CENSUS shipped_cells=84 shipped_shapes=1464317 "
           "reference_cells={c} reference_shapes={s}")


def test_a_reference_missing_a_library_is_refused_not_compared():
    """MEASURED TWICE: a bare DEF read gave 776,403 differences and a re-stream
    that lost both macro libraries gave 348,392 -- each a confident FAIL about the
    design, neither about the design. The counts never revealed it; the census
    does."""
    ok, why, stats = g.reference_is_faithful(_CENSUS.format(c=62, s=10240))
    assert ok is False
    assert stats["reference_shapes"] == 10240
    assert "did not resolve" in why


def test_a_plausible_reference_is_compared():
    """The real re-stream: 1,203,581 shapes against 1,464,317 -- smaller, because
    fill and the seal ring are not in it yet, but not by an order of magnitude."""
    ok, why, stats = g.reference_is_faithful(_CENSUS.format(c=62, s=1203581))
    assert ok is True and why == ""
    assert stats["shipped_shapes"] == 1464317


def test_no_census_is_refused_rather_than_trusted():
    ok, why, stats = g.reference_is_faithful("XOR_LAYER 32/0 1\nXOR_DONE\n")
    assert ok is False and stats == {}
    assert "no census" in why


# --- the re-stream inputs: the two defects that produced a wrong reference ----

def test_macro_gds_is_joined_with_the_separator_the_recipe_parses(tmp_path):
    """MEASURED DEFECT: the recipe reads MACRO_GDS as `.split(';')`. Joining the
    two recorded libraries with a SPACE handed it one unopenable path, both IO
    libraries were silently lost, and the only symptom was a small reference."""
    log = tmp_path / g.STREAMOUT_LOG_GLOB.replace("*", "")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "LEFDEF_MAP applied: /pdk/tech.map\n"
        "CELL_GDS manual-substitute (post-read): /pdk/cells.gds\n"
        "CELL_GDS manual-substituted 229 std cell(s)\n"
        "MACRO_GDS manual-substituted 2 macro(s): /pdk/ef_io.gds\n"
        "MACRO_GDS manual-substituted 14 macro(s): /pdk/fd_io.gds\n")
    env, cited = g.restream_env_from_transcript(tmp_path)
    assert env["MACRO_GDS"] == "/pdk/ef_io.gds;/pdk/fd_io.gds"
    assert " " not in env["MACRO_GDS"]
    assert env["CELL_GDS"] == "/pdk/cells.gds"
    assert len(cited) == 3


def test_the_magic_engine_transcript_is_read_too(tmp_path):
    """THE ENGINE SPLIT, and it is why the read is a glob.

    `stream_out.log` is written by the KLAYOUT stream-out only; the Magic engine
    beside it writes `<top>.magic_stream_out.log`. Reading only the KLayout name
    meant a Magic-streamed run recovered NO library hints at all, so its
    re-streamed reference came out thin -- refused by `reference_is_faithful`
    rather than answered. Same glob the flow declares and step 37's gate asserts.
    """
    log = tmp_path / "phase3/stage3/pnr/spm.magic_stream_out.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "LEFDEF_MAP applied: /pdk/tech.map\n"
        "CELL_GDS manual-substitute (post-read): /pdk/cells.gds\n"
        "MACRO_GDS manual-substituted 2 macro(s): /pdk/ef_io.gds\n")
    env, cited = g.restream_env_from_transcript(tmp_path)
    assert env["CELL_GDS"] == "/pdk/cells.gds", env
    assert env["MACRO_GDS"] == "/pdk/ef_io.gds", env
    assert any("magic_stream_out.log" in c for c in cited), cited


def test_the_negative_productions_of_the_transcript_are_not_read_as_values(
        tmp_path):
    """THE POLARITY CLAIM, asserted rather than argued in a comment.

    The recipe DOES say the negative, in productions the anchored patterns cannot
    reach. Every one of them is written here; not one may yield a key, because a
    key that came from a denial would be handed to the re-stream as a real path.
    """
    log = tmp_path / "phase3/stage3/pnr/stream_out.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "LEFDEF_MAP not applied (none configured) - legacy numbering\n"
        "LEFDEF_MAP_CONFIGURED_BUT_ABSENT: the PDK configured a map\n"
        "CELL_GDS manual-substituted 229 std cell(s)\n"
        "MACRO_GDS merged (no DEF master matched): /pdk/ef_io.gds\n")
    env, cited = g.restream_env_from_transcript(tmp_path)
    assert env == {}, env
    assert cited == [], cited


def test_a_recorded_host_path_is_reanchored_to_this_project(tmp_path):
    """MEASURED DEFECT: `technology_units.json.tech_lef` is an absolute path in the
    ORIGINAL run root. Read on a copy it pointed into another lane's directory --
    which this program must not read -- and the LEF silently failed to open."""
    real = tmp_path / "phase3/stage3/pnr/active_via_legalized.tlef"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("VERSION 5.8 ;\n")
    # ANOTHER run root, absolute and outside this project -- derived from
    # tmp_path rather than spelled as a personal home, which the shipped-path
    # portability gates refuse in shipped source (test_path_lint_fixtures_are_
    # host_portable, test_shipped_path_portability_check).
    other = tmp_path.parent / (tmp_path.name + "_lane_other") / "run21"
    recorded = str(other / "phase3/stage3/pnr/active_via_legalized.tlef")
    assert not Path(recorded).exists()
    assert g.reanchored(tmp_path, recorded) == str(real)


def test_reanchoring_never_invents_a_file(tmp_path):
    """Existence-tested: with no such tail under the project the recorded path is
    returned unchanged, so the caller's own open-failure names the real path
    instead of a plausible-looking one this function made up."""
    other = tmp_path.parent / (tmp_path.name + "_lane_other") / "run21"
    recorded = str(other / "phase3/stage3/pnr/absent.tlef")
    assert g.reanchored(tmp_path, recorded) == recorded


def test_a_path_that_already_resolves_is_left_alone(tmp_path):
    here = tmp_path / "keep.tlef"
    here.write_text("x")
    assert g.reanchored(tmp_path, str(here)) == str(here)


# --- the reference kind is always named --------------------------------------

def _staged_boundary(project, top="spm", body=b"GDS", *, with_receipt=True,
                     def_file=None):
    """A retained boundary as the RUNNER now stages it: the artefact PLUS the
    receipt that binds it to the run that wrote it (R-0915-148).

    The fixture gained the receipt because the resolver gained a proof
    requirement: only the KLayout stream-out branch retains a boundary, the Magic
    branch retained nothing and deleted nothing, and nothing ever unlinked one --
    so an artefact alone could be a LEFTOVER of an earlier invocation, and calling
    it "design-layer differences expected to be exactly 0" turned any routing
    change between two runs into a design FAIL about the wrong layout. The claim
    below is unchanged; what changed is that the fixture now stages a boundary the
    run can PROVE is its own, which is what the claim was always about.
    """
    import hashlib
    import json as _json
    kept_rel = g.PREFINISH_REL_FMT.format(top=top)
    kept = project / kept_rel
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(body)
    if with_receipt:
        rec = {"program": "phase3_one_shot_runner", "artefact": kept.name,
               "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
               "engine": "klayout"}
        if def_file is not None:
            rec["streamed_from_def"] = def_file.name
            rec["def_sha256"] = hashlib.sha256(def_file.read_bytes()).hexdigest()
        kept.with_suffix(".gds.receipt.json").write_text(_json.dumps(rec) + "\n")
    return kept, kept_rel


def _def_named(project, filename="spm.def", design="spm"):
    d = project / "phase3/stage3/pnr" / filename
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(f"VERSION 5.8 ;\nDESIGN {design} ;\nCOMPONENTS 0 ;\nEND DESIGN\n")
    return d


def test_the_retained_boundary_is_preferred_and_named(tmp_path):
    dfile = _def_named(tmp_path)
    kept, kept_rel = _staged_boundary(tmp_path, def_file=dfile)
    path, kind, prov = g.resolve_reference(tmp_path, "spm", dfile)
    assert kind == "retained" and path == kept
    assert prov["sha256"] and prov["path"] == kept_rel
    assert "exactly 0" in prov["note"]


def test_a_boundary_the_run_cannot_prove_is_re_streamed_not_preferred(tmp_path):
    """THE NEGATIVE BESIDE IT, and the reason the fixture above now carries a
    receipt: an artefact with nothing tying it to this run is exactly the leftover
    case, and preferring it would assert zero differences about another run's
    layout. It re-streams, and the kind is recorded so no reader mistakes one for
    the other."""
    dfile = _def_named(tmp_path)
    _staged_boundary(tmp_path, def_file=dfile, with_receipt=False)
    path, kind, prov = g.resolve_reference(tmp_path, "spm", dfile)
    assert path is None and kind == "unprovable", (kind, prov)
    assert "cannot be proven" in prov["note"]


def test_without_the_boundary_the_receipt_says_it_re_streamed(tmp_path):
    """run21 and earlier have no boundary artefact. The receipt must not let a
    re-streamed reference be mistaken for the retained one, because only the
    retained path carries a hard expectation of zero."""
    path, kind, prov = g.resolve_reference(tmp_path, "spm")
    assert path is None and kind == "restreamed"
    assert "attributed per layer" in prov["note"]


def test_an_absent_recipe_refuses_rather_than_substituting_one(tmp_path):
    """A stream-out this program wrote itself would be measuring its own
    conventions. MEASURED: a bare `Layout.read(def)` did exactly that, for 776,403
    phantom differences."""
    rc, _so, se = g.stream_reference(None, tmp_path / "scratch",
                                     tmp_path / "spm.def",
                                     tmp_path / "out.gds", 60)
    assert rc == 127
    assert "will not substitute one of its own" in se


# --- the consumer: a refusal is passed through, never collapsed to zero -------

def _agg():
    import signoff_metrics_aggregate as s
    return s


def test_the_metric_reads_the_producers_count(tmp_path):
    """R-0915-129 metric 2: the key was NOT_MEASURED for every design because no
    step produced it. Now one does, and the aggregator states its number."""
    s = _agg()
    _write(tmp_path, s._XOR_REL,
           {"verdict": "PASS", "design__xor_difference__count": 0,
            "reference": {"kind": "retained"},
            "finishing_layer_differences": [{"layer": 34, "datatype": 4,
                                             "differences": 20291}]})
    cell = s._xor(tmp_path)
    assert cell.value == 0
    assert cell.source == s._XOR_REL
    assert "retained" in cell.basis and "1 finishing-declared" in cell.basis


def test_a_nonzero_count_is_reported_as_measured(tmp_path):
    s = _agg()
    _write(tmp_path, s._XOR_REL,
           {"verdict": "FAIL", "design__xor_difference__count": 1,
            "reference": {"kind": "restreamed"}})
    assert s._xor(tmp_path).value == 1


def test_a_producer_refusal_is_not_a_zero(tmp_path):
    """THE CASE THIS KEY EXISTS FOR. The producer refuses when the reference it
    can build is not faithful to the shipped artefact. Reading that as 0 would
    publish a clean fidelity result for a comparison that never happened."""
    s = _agg()
    _write(tmp_path, s._XOR_REL,
           {"verdict": "NOT_DETERMINED",
            "reason": "refusing to compare: the reference carries 10240 shape(s)"})
    cell = s._xor(tmp_path)
    assert cell.value == s.NOT_MEASURED
    assert "10240" in cell.reason


def test_an_absent_receipt_is_not_a_zero(tmp_path):
    s = _agg()
    cell = s._xor(tmp_path)
    assert cell.value == s.NOT_MEASURED
    assert "an unrun XOR is not a difference count of zero" in cell.reason
