"""U23: the per-layer density FILL measures on the stream that ships must be
where the tape-out precheck's density delegate reads it.

Measured on a subservient DIE tree (FILL proof lane, 8hd-3, 2026-09-29):
`phase3/librelane/37-proof-final-density-ratios/density_ratios.json` held a
MEASURED per-rule density of the finished stream, while general_precheck's
Checker.KLayoutDensity ran `metal_layer_density_check reports/phase3` and came
back NOT_DETERMINED: "no density report at .../reports/phase3". The measured
rows are carried verbatim in `data/u23_density_path/density_ratios.json`.

One canonical place (`reports/phase3/metal_density.json`, where the direct
path's emitter and every reader already put it): the producer moves, the reader
is not taught a second path.
"""
import hashlib
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import general_precheck as GP  # noqa: E402
import librelane_fill_dfm as fill  # noqa: E402
import librelane_step37 as step37  # noqa: E402
from test_die_density_finish import _pdk, _put  # noqa: E402

MEASURED = json.loads(
    (Path(__file__).parent / "data/u23_density_path/density_ratios.json").read_text())


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _promote(tmp_path, monkeypatch, ratios_for=None):
    """Drive the real `librelane_step37.run`; only the EDA tools' writes are
    faked (each LibreLane step's state_out.json and the density-ratio lane
    report with the measured subservient rows)."""
    import librelane_pv_signoff as pv

    project = tmp_path / "project"
    project.mkdir()
    root, cfg = _pdk(tmp_path)
    configs = {step: cfg for step in step37.STEPS}
    source = project / "source.gds"
    source.write_bytes(b"stream")
    sealed = project / "sealed.gds"
    sealed.write_bytes(b"sealed")
    filled = project / "filled.gds"
    filled.write_bytes(b"sealed plus pdk fill")
    state = _put(project / "state.json", {"gds": str(source)})
    routed = project / "routed.def"
    routed.write_text("DEF")
    monkeypatch.setattr(step37, "resolve_step_configs", lambda *a, **k: configs)
    monkeypatch.setattr(step37, "declaration_config", lambda *a: (
        {"CORE_AREA": [10, 10, 90, 90]}, {"CORE_AREA": "test declaration"}))
    monkeypatch.setattr(step37, "_routed_state", lambda *a: state)
    monkeypatch.setattr(step37, "_vibeic_gds_gates", lambda *a: {
        "substance": {"rc": 0, "sha256": "x"}, "port_labels": {"rc": 0, "sha256": "x"}})
    monkeypatch.setattr(step37, "_measured_drc", lambda *a: {
        "magic": 0, "klayout": 0, "total": 0})
    monkeypatch.setattr(pv, "tech_lef_overlay", lambda *a: None)
    monkeypatch.setattr(pv, "run_finishing_xor", lambda *a, **k: {"verdict": "PASS"})

    def ratios(project, image, pdk_root, pdk, gds, density_config, lane):
        report = project / "phase3/librelane" / lane / "density_ratios.json"
        _put(report, {"gds": str(gds), "die_area_um2": MEASURED["die_area_um2"],
                      "layers": MEASURED["layers"], "status": MEASURED["status"]})
        subject = (ratios_for or (lambda g: _sha(g)))(gds)
        return {"report": str(report), "report_sha256": _sha(report),
                "subject": str(gds), "subject_sha256": subject,
                "layers": MEASURED["layers"]}

    monkeypatch.setattr(fill, "measure_density_ratios", ratios)

    def eda_run(project, image, pdk_root, pdk, steps, incoming, lane, configs):
        parent = json.loads(Path(incoming).read_text())
        folders = []
        for index, name in enumerate(steps, 1):
            folder = (project / "phase3/librelane" / lane /
                      f"{index:02d}-{name.lower().replace('.', '-')}")
            nxt = dict(parent)
            if name == "KLayout.XOR":
                nxt.update({"mag_gds": str(source), "klayout_gds": str(source),
                            "metrics": {"design__xor_difference__count": 0}})
            if name == "KLayout.SealRing":
                nxt["gds"] = str(sealed)
            if name == "KLayout.Filler":
                nxt["gds"] = str(filled)
            if name == "KLayout.Density":
                nxt["metrics"] = {"klayout__density_error__count": 0}
            _put(folder / "state_out.json", nxt)
            parent = nxt
            folders.append(folder)
        return folders

    monkeypatch.setattr(step37, "_run", eda_run)
    canonical = project / "phase3/stage4/gds/chip_top.gds"
    canonical.parent.mkdir(parents=True)
    result = step37.run(project, "image", root, "processA", routed,
                        project / "routed.v", project / "routed.sdc", canonical)
    return project, canonical, result


def _precheck_density(project):
    step = next(s for s in GP.LADDER if s.step_id == "Checker.KLayoutDensity")
    ev = GP._blank(step)
    GP._step_delegate(ev, step, project, GP.default_runner, GP._HERE, 120,
                      pdk="gf180mcuD")
    return ev


def test_precheck_reads_the_density_fill_measured_on_the_shipped_stream(
        tmp_path, monkeypatch):
    project, canonical, result = _promote(tmp_path, monkeypatch)
    # The lane report exists either way; the question is whether the reader's
    # one place carries it.
    assert list((project / "phase3/librelane").glob("37-*-density-ratios/density_ratios.json"))
    ev = _precheck_density(project)
    assert "no density report" not in ev.evidence, ev.evidence
    assert ev.verdict in (GP.PASS, GP.FAIL), (ev.verdict, ev.evidence)
    published = json.loads((project / fill.METAL_DENSITY_REL).read_text())
    assert published["gds_sha256"] == _sha(canonical)
    rows = MEASURED["layers"]
    assert published["layers"] == {rows[r]["identifier"]: rows[r]["ratio"]
                                   for r in ("M1.4", "M2.4", "M3.4", "M4.4", "M5.4")}
    promo = json.loads(Path(result["promotion"]).read_text())
    assert promo["metal_density"] == str(project / fill.METAL_DENSITY_REL)
    # The delegate judged exactly those layers.
    judged = json.loads((project / "reports/phase3/general_precheck/"
                         "precheck_density.json").read_text())
    assert set(judged.get("per_layer") or {}) == set(published["layers"]), judged


def test_a_ratio_measured_on_other_bytes_is_not_published(tmp_path, monkeypatch):
    project, _canonical, _result = _promote(
        tmp_path, monkeypatch, ratios_for=lambda g: "0" * 64)
    assert not (project / fill.METAL_DENSITY_REL).exists()
    ev = _precheck_density(project)
    assert ev.verdict not in (GP.PASS,), ev.evidence


def test_two_values_for_one_layer_publish_nothing(tmp_path):
    gds = tmp_path / "chip.gds"
    gds.write_bytes(b"x")
    ratios = {"subject_sha256": _sha(gds), "layers": {
        "M1.4": {"status": "MEASURED", "identifier": "metal1", "ratio": 0.4},
        "M1.9": {"status": "MEASURED", "identifier": "metal1", "ratio": 0.5}}}
    assert fill.publish_metal_density(tmp_path, ratios, gds, "p") is None
    assert not (tmp_path / fill.METAL_DENSITY_REL).exists()
