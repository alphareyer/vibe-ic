"""A converter's digital datapath is GENERATED from declared parameters, or
REFUSED by the name of the field that was not declared.

WHAT WENT WRONG (vibe-ic#2197)
==============================
`data_converter` carried ``rtl_gen: null``, so for a converter WITH a digital
datapath the only reachable outcome was an LLM authoring hand-off. MEASURED on
live main ``9c653d47f1``, in the pinned image, by execution::

    status      : WAIVED      fallback : spec-to-rtl
    rtl dir     : False       (phase2/stage1/rtl never created)
    registry rtl_gen : None
    refusal-by-name sources : NONE

The flow could neither author the datapath nor say what it lacked. One root
cause, two halves, and this file pins both.

WHY A CIC AND NOTHING ELSE
==========================
A decimator's order, band edges and coefficient set are design decisions a
specification does not state; inventing them produces something plausible and
indistinguishable from correct until silicon (§4.05). A cascaded integrator-comb
has NO coefficients — every dimension is arithmetic on a declared parameter —
so it is the one structure derivable without inventing anything. The generator
emits that and refuses the rest.

WHAT THIS FILE LOCKS
====================
1. The class DECLARES a generator (the registry entry is the wiring; without it
   the program is unreachable no matter how correct it is).
2. Fully declared -> emits, and the emitted structure EQUALS the declared
   parameters, with Hogenauer's exact register growth.
3. THE POLARITY CONTROL: each declared field removed ALONE flips the verdict to
   a refusal that NAMES that field. A generator that emitted regardless of its
   inputs would satisfy (2) and be worthless.
4. The refusal is a REFUSAL, not a silent pass: rc != 0 and no RTL on disk.
5. ANOTHER CLASS IS UNAFFECTED — the registry change is scoped to one row.

Run::

    cd .../plugins/vibe-ic && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest \\
      programs/tests/test_issue2197_data_converter_datapath_generator.py -q
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_converter_rtl_gen as G                     # noqa: E402
import design_one_shot_runner as DOR                   # noqa: E402

#: One fully-declared converter. Values are arbitrary positive integers: the
#: point is that they are DECLARED, not what they are.
FULL = {"osr": 64, "decimator_stages": 3,
        "decimator_differential_delay": 1, "bitstream_width": 1}

#: A second design OF THE SAME CLASS with different declared numbers — item 3
#: of the issue asks for two, and one design proves a constant just as well as
#: a function.
FULL_B = {"osr": 32, "decimator_stages": 4,
          "decimator_differential_delay": 2, "bitstream_width": 3}


def _mk(root: Path, rows: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    docs = root / "phase1" / "generated_docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "L5_ADI_SPEC.json").write_text(json.dumps(
        {"analog_blocks": [{"name": "modulator", "spec": {"specs": [
            {"name": k, "target": v} for k, v in rows.items()]}}]}),
        encoding="utf-8")
    return root


# ── 1. the wiring ──────────────────────────────────────────────────────────

def test_the_class_declares_this_generator():
    """Without the registry row the program is unreachable, however correct."""
    cfg = DOR._lookup_class("data_converter") or {}
    assert cfg.get("rtl_gen") == "data_converter_rtl_gen.py", cfg.get("rtl_gen")
    assert (Path(__file__).resolve().parent.parent
            / "data_converter_rtl_gen.py").is_file()


def test_the_declared_fallback_author_is_retained():
    """The generator does not REPLACE the author hand-off; a refusal still has
    somewhere to go, which is what makes refusing safe."""
    cfg = DOR._lookup_class("data_converter") or {}
    assert cfg.get("fallback_skill") == "spec-to-rtl"


# ── 2. declared in full -> emitted, and the structure IS the declaration ───

@pytest.mark.parametrize("rows", [FULL, FULL_B], ids=["designA", "designB"])
def test_a_fully_declared_converter_is_generated(tmp_path, rows):
    p = _mk(tmp_path / "d", rows)
    assert G.main([str(p)]) == 0
    sv = (p / "phase2" / "stage1" / "rtl" / "cic_decimator.sv")
    assert sv.is_file()
    text = sv.read_text(encoding="utf-8")
    wout = (int(math.ceil(rows["decimator_stages"]
                          * math.log2(rows["osr"]
                                      * rows["decimator_differential_delay"])))
            + rows["bitstream_width"])
    for want in (f"parameter int DECIM_R = {rows['osr']}",
                 f"parameter int STAGES  = {rows['decimator_stages']}",
                 f"parameter int DIFF_M  = {rows['decimator_differential_delay']}",
                 f"parameter int WIN     = {rows['bitstream_width']}",
                 f"parameter int WOUT    = {wout}"):
        assert want in text, f"{want!r} missing from:\n{text[:1500]}"


def test_the_register_growth_is_hogenauers_arithmetic():
    """Not a table and not a guess: ceil(N*log2(R*M)) + Win, checked against an
    independent evaluation for a spread of declared shapes."""
    for r, n, m, win in ((64, 3, 1, 1), (32, 4, 2, 3), (8, 1, 1, 8),
                         (256, 5, 2, 1), (2, 2, 1, 16)):
        assert G.output_width({"decimation_factor": r, "stages": n,
                               "differential_delay": m, "input_width": win}) \
            == int(math.ceil(n * math.log2(r * m))) + win


def test_no_filter_coefficient_is_invented(tmp_path):
    """The §4.05 property, asserted on the artefact: a CIC has unity taps, so
    the emitted file must contain no coefficient table. If a later change
    starts emitting a compensator, this fails and asks for the proof."""
    p = _mk(tmp_path / "d", FULL)
    assert G.main([str(p)]) == 0
    text = (p / "phase2/stage1/rtl/cic_decimator.sv").read_text(encoding="utf-8")
    # COMMENTS ARE STRIPPED FIRST, and that is the point rather than a
    # convenience: the header comment SAYS "contains no filter coefficient",
    # and a scan of the raw text therefore failed on the sentence promising the
    # property it was checking. The property is about the emitted LOGIC.
    code = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    code = re.sub(r"//[^\n]*", " ", code).lower()
    assert "module" in code and "always_ff" in code, code[:400]
    for banned in ("coeff", "taps", "firdes", "passband", "stopband"):
        assert banned not in code, f"{banned!r} appears in generated LOGIC"


# ── 3. THE POLARITY CONTROL ────────────────────────────────────────────────

@pytest.mark.parametrize("missing", sorted(FULL))
def test_each_declared_field_removed_alone_flips_it_to_a_named_refusal(
        tmp_path, missing, capsys):
    rows = {k: v for k, v in FULL.items() if k != missing}
    p = _mk(tmp_path / "d", rows)
    rc = G.main([str(p)])
    err = capsys.readouterr().err
    assert rc == 2, f"removing {missing!r} did not refuse (rc={rc})"
    assert "I cannot build this because the specification does not state" in err
    field = next(f for f, names, _ in G.DECLARED_FIELDS if missing in names)
    assert field in err, f"the refusal does not name {field!r}:\n{err}"
    assert not (p / "phase2/stage1/rtl").exists(), "a refusal wrote RTL"


def test_a_non_integer_declaration_is_refused_not_rounded(tmp_path):
    """Rounding a declared value would be the invention this file exists to
    prevent — a filter with 2.5 stages is not a structure."""
    p = _mk(tmp_path / "d", {**FULL, "decimator_stages": 2.5})
    assert G.main([str(p)]) == 2


def test_an_absent_specification_refuses_by_name(tmp_path):
    (tmp_path / "empty").mkdir()
    assert G.main([str(tmp_path / "empty")]) == 2


# ── 4. the refusal reaches the runner as a refusal, not a pass ─────────────

def test_the_runner_turns_the_refusal_into_a_named_failure(tmp_path):
    """`step_rtl_gen` maps a non-zero generator to FAIL and routes to the
    declared author. The refusal is therefore REACHABLE end to end, and is
    never a silent pass."""
    p = _mk(tmp_path / "d", {k: v for k, v in FULL.items() if k != "osr"})
    gen = (Path(__file__).resolve().parent.parent / "data_converter_rtl_gen.py")
    r = subprocess.run([sys.executable, str(gen), str(p)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "does not state" in r.stderr
    assert not (p / "phase2/stage1/rtl").exists()


# ── 5. the control: another class is untouched ─────────────────────────────

def test_another_class_is_unaffected_by_the_registry_change():
    """The issue asks for one design of another class that must not move. The
    registry is the only shared object this change touches, so the assertion is
    that every OTHER row's generator is exactly what it was."""
    others = {c["name"]: c.get("rtl_gen")
              for c in DOR._REGISTRY_CLASSES()  # type: ignore[attr-defined]
              } if hasattr(DOR, "_REGISTRY_CLASSES") else None
    if others is None:
        reg = json.loads((Path(__file__).resolve().parent.parent
                          / "ic_class_registry.json").read_text(encoding="utf-8"))
        others = {c["name"]: c.get("rtl_gen") for c in reg["classes"]}
    assert others.get("pure_analog") is None
    assert others.get("crypto_accelerator") is None
    assert others.get("aid_class_half_duplex_single_wire") == "aid_class_rtl_gen.py"
    assert others.get("mixed_signal_otp") == "aid_class_rtl_gen.py"
    generated = {k for k, v in others.items() if v}
    assert generated == {"aid_class_half_duplex_single_wire",
                         "mixed_signal_otp", "data_converter"}, generated


def test_a_pure_analog_project_is_not_served_by_this_generator(tmp_path):
    """A design of another class, run through this generator, refuses by name
    rather than emitting something. The generator is not a universal author."""
    p = tmp_path / "analog"
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "phase1/generated_docs/L5_ADI_SPEC.json").write_text(json.dumps(
        {"analog_blocks": [{"name": "ldo", "spec": {"specs": [
            {"name": "vout", "target": 1.8}]}}]}), encoding="utf-8")
    assert G.main([str(p)]) == 2
    assert not (p / "phase2/stage1/rtl").exists()


# ── 6. the routing must not depend on whether a generator exists ───────────

def _no_subject_project(tmp_path):
    """A converter whose interface and Phase-1 content both say: no datapath."""
    import test_clocked_but_logic_free_has_no_rtl_subject as T
    return T._mk_project(tmp_path / "nosubject", T._CLOCKED_LOGIC_FREE)


def test_a_converter_with_no_datapath_still_routes_to_the_analog_track(tmp_path):
    """THE REGRESSION THIS BRANCH CAUSED AND THEN FIXED.

    ORGANIC #141 / #2184 placed the analog deferral INSIDE the ``rtl_gen is
    null`` arm, so it asked "does the CLASS have a generator?" before "does the
    DESIGN have a datapath?". The moment `data_converter` gained one, MEASURED
    on this branch before the hoist::

        no subject -> FAIL   deferred_to=None      (was WAIVED/analog_track)

    A design with nothing to author was handed to a generator that correctly
    refused it, and the refusal was published as a failure of the design. The
    deferral is now evaluated BEFORE the generator/null split.
    """
    res = DOR.step_rtl_gen(_no_subject_project(tmp_path), "data_converter")
    assert res.status == "PASS_WITH_WAIVERS", res.detail
    assert res.extras.get("deferred_to") == "analog_track"
    assert res.extras.get("fallback_skill") is None
    assert res.extras.get("digital_datapath_absent") is True


def test_the_deferral_is_reached_for_a_class_that_declares_a_generator():
    """The property stated directly: the class under test HAS a generator, and
    that is exactly the configuration in which the old placement failed. A test
    that only exercised a generator-less class could not have caught this."""
    cfg = DOR._lookup_class("data_converter") or {}
    assert cfg.get("rtl_gen"), "precondition: the class declares a generator"
    assert callable(getattr(DOR, "_analog_track_deferral", None)), (
        "the deferral must be its own function, reachable before the "
        "generator/null split — inlined in one arm it is not")


def test_item4_removing_the_generator_returns_the_routed_to_analog_behaviour(
        tmp_path, monkeypatch):
    """Issue item 4, driven rather than asserted: with the registry row removed
    the flow returns to routing this design to the analog track — NOT to a
    silent pass. Because the deferral no longer sits behind the generator
    check, the verdict is IDENTICAL with and without it."""
    p = _no_subject_project(tmp_path)
    with_gen = DOR.step_rtl_gen(p, "data_converter")

    real = DOR._lookup_class

    def _without_generator(name):
        cfg = dict(real(name) or {})
        if name == "data_converter":
            cfg["rtl_gen"] = None
        return cfg

    monkeypatch.setattr(DOR, "_lookup_class", _without_generator)
    without_gen = DOR.step_rtl_gen(_no_subject_project(tmp_path / "b"),
                                   "data_converter")
    assert with_gen.status == without_gen.status == "PASS_WITH_WAIVERS"
    assert (with_gen.extras.get("deferred_to")
            == without_gen.extras.get("deferred_to") == "analog_track")
