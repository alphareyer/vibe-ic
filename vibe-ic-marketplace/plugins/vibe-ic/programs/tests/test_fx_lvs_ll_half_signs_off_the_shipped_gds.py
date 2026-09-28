"""Step 31's LibreLane half must judge the layout that SHIPS too (lane fxlvs, r2).

THE DEFECT. `librelane_pv_signoff.run_half("lvs")` runs LibreLane's
`Magic.SpiceExtraction` -> `Netgen.LVS` on a bridged State that carries the
shipped GDS, but the step's own `MAGIC_EXT_USE_GDS` defaults to False in the
image (MEASURED, vibeic-eda 0.3.83, LibreLane 3.1.0.dev1: declared, bool,
default False), so Magic reads the ROUTED DEF over LEF abstracts. With step 31
on `librelane` the unlabelled spm.gds that fails pin matching against the
PDK's own decks (29 mismatches) was never opened.

THE RULE NOW. The LVS half resolves `Magic.SpiceExtraction` with
`MAGIC_EXT_USE_GDS = True` (an overlay with its source) and, before any tool
runs, reads from the image's own declaration -- the step's declared variables
and their defaults, which the resolution now records beside each config --
that the variable exists and that the resolved value is True. An image whose
step does not declare it, or a resolution that did not set it, refuses: step 31
is NOT_MEASURED and `lvs_verdict.json` BLOCKED, never a PASS. So is a missing
shipped view.

Driven through the REAL `resolve_step_configs` (its in-image script executed
against a stand-in `librelane` package that declares what the image declares),
the real bridge and the real `run_chain`; only the tools' file writes are
faked, and the extraction answers from the file the configured variable makes
it read. chip/PDK-AGNOSTIC: synthetic design, synthetic PDK name.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
contract = importlib.import_module("librelane_contract")
pv = importlib.import_module("librelane_pv_signoff")

_REAL_RUN = subprocess.run
PDK = "procA"
#: The shipped layout's bytes. Matched on the extracted netlist's content line,
#: never on a path (pytest names tmp dirs after the test).
UNLABELLED = "layout-bytes: LAYOUT-WITHOUT-CELL-PIN-LABELS"
LABELLED = "layout-bytes: LAYOUT-WITH-CELL-PIN-LABELS"

#: What the image's LibreLane declares for the two LVS steps (MEASURED on
#: 0.3.83 for the variables named here; the rest of each step is irrelevant).
IMAGE_STEPS = {
    "Magic.SpiceExtraction": {
        "inputs": ["gds", "def"], "outputs": ["spice"],
        "variables": {"DESIGN_NAME": None, "TECH_LEFS": None,
                      "MAGIC_EXT_USE_GDS": False, "MAGIC_EXT_ABSTRACT": False,
                      "MAGIC_EXT_UNIQUE": "all"}},
    "Netgen.LVS": {
        "inputs": ["spice", "pnl"], "outputs": [],
        "variables": {"DESIGN_NAME": None, "TECH_LEFS": None}},
    "Magic.DRC": {"inputs": ["gds"], "outputs": [],
                  "variables": {"DESIGN_NAME": None, "TECH_LEFS": None}},
    "KLayout.DRC": {"inputs": ["gds"], "outputs": [],
                    "variables": {"DESIGN_NAME": None, "TECH_LEFS": None}},
    "KLayout.Density": {"inputs": ["gds"], "outputs": [],
                        "variables": {"DESIGN_NAME": None, "TECH_LEFS": None}},
}

#: The design-independent PDK values LibreLane's resolver supplies.
PDK_VALUES = {"TECH_LEFS": {"nom_*": "/pdk/procA/t.tlef"}}

_STUB = {
    "librelane/__init__.py": "__version__ = '3.1.0-stub'\n",
    "librelane/__version__.py": "__version__ = '3.1.0-stub'\n",
    "librelane/flows/__init__.py": "",
    "librelane/flows/chip.py": textwrap.dedent('''\
        import json, os
        from librelane.steps import _TABLE, _PDK_VALUES

        class _Config:
            def __init__(self, raw):
                self._raw = raw
            def to_raw_dict(self):
                return dict(self._raw)

        class Chip:
            gating_config_vars = {}
            def __init__(self, config, pdk, pdk_root, design_dir):
                declared = json.loads(open(config).read())
                raw = {}
                for step in _TABLE.values():
                    raw.update({k: v for k, v in step["variables"].items()
                                if v is not None})
                raw.update(_PDK_VALUES)
                raw.update(declared)          # the design wins over defaults
                raw.setdefault("DESIGN_NAME", "chip")
                if not raw.get("VERILOG_FILES"):  # as LibreLane 3.1's resolver refuses
                    raise SystemExit("InvalidConfig: Required variable "
                                     "'VERILOG_FILES' did not get a specified value.")
                self.config = _Config(raw)
        '''),
    "librelane/steps/__init__.py": textwrap.dedent('''\
        import json, os
        _TABLE = json.loads(open(os.environ["STUB_IMAGE_STEPS"]).read())
        _PDK_VALUES = json.loads(os.environ["STUB_PDK_VALUES"])

        class _Var:
            def __init__(self, name, default):
                self.name, self.default = name, default

        class _Fmt:
            def __init__(self, id):
                self.id = id

        class _Step:
            def __init__(self, sid, decl):
                self.id = sid
                self.inputs = [_Fmt(x) for x in decl["inputs"]]
                self.outputs = [_Fmt(x) for x in decl["outputs"]]
                self._vars = [_Var(k, v) for k, v in decl["variables"].items()]
            def get_all_config_variables(self):
                return list(self._vars)

        class _Factory:
            def get(self, sid):
                decl = _TABLE.get(sid)
                return None if decl is None else _Step(sid, decl)

        class Step:
            factory = _Factory()
        '''),
}


def _put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


class Image:
    """`docker run` of the image, faked at the boundary.

    * the config resolution runs the contract's OWN in-image script, against a
      stand-in `librelane` that declares `steps` (what the image declares);
    * an OpenROAD bridge session writes what its Tcl names;
    * `Magic.SpiceExtraction` extracts the GDS or the DEF -- whichever the
      config it was handed selects -- and `Netgen.LVS` fails pin matching on a
      layout whose bytes say `unlabelled`, exactly as the real pair does on the
      unlabelled spm.gds (29 pin mismatches)."""

    def __init__(self, tmp_path, steps):
        self.root = tmp_path / "stub"
        for rel, text in _STUB.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)
        self.table = _put(tmp_path / "image_steps.json", steps)
        self.resolved: list[dict] = []
        self.ran: list[str] = []

    def __call__(self, cmd, **kwargs):
        if "-c" in cmd and "--entrypoint" in cmd and "librelane.flows.chip" in " ".join(cmd):
            i = cmd.index("-c")
            env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(self.root),
                   "STUB_IMAGE_STEPS": str(self.table),
                   "STUB_PDK_VALUES": json.dumps(PDK_VALUES)}
            done = _REAL_RUN([sys.executable, "-c", cmd[i + 1], *cmd[i + 2:]],
                             capture_output=True, text=True, env=env, timeout=120)
            self.resolved.append({"design": json.loads(Path(cmd[i + 2]).read_text())})
            return done
        if "--id" in cmd:
            folder = Path(cmd[cmd.index("-o") + 1])
            step = cmd[cmd.index("--id") + 1]
            config = json.loads(Path(cmd[cmd.index("-c") + 1]).read_text())
            incoming = json.loads(Path(cmd[cmd.index("-i") + 1]).read_text())
            self.ran.append(step)
            _put(folder / "state_in.json", incoming)
            out = dict(incoming, metrics=dict(incoming.get("metrics") or {}))
            if step == "Magic.SpiceExtraction":
                source = incoming["gds"] if config.get("MAGIC_EXT_USE_GDS") else incoming["def"]
                layout = Path(source).read_text(errors="replace")
                (folder / "chip.spice").write_text(
                    f"* extracted from {source}\n* {layout}\n")
                out["spice"] = str(folder / "chip.spice")
                out["metrics"]["magic__illegal_overlap__count"] = 0
            if step == "Netgen.LVS":
                pins = 29 if UNLABELLED in Path(incoming["spice"]).read_text() else 0
                reports = folder / "reports"
                reports.mkdir(parents=True, exist_ok=True)
                (reports / "lvs.netgen.rpt").write_text(
                    "Final result: Top level cell failed pin matching.\n" if pins
                    else "Final result: Circuits match uniquely.\n")
                out["metrics"].update({"design__lvs_error__count": pins,
                                       "design__lvs_unmatched_device__count": 0,
                                       "design__lvs_unmatched_net__count": 0,
                                       "design__lvs_unmatched_pin__count": pins})
            for key in pv.PRODUCED.get(step, ()):
                out["metrics"].setdefault(key, 0)
            _put(folder / "state_out.json", out)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "openroad" in cmd:
            for line in Path(cmd[-1]).read_text().splitlines():
                for verb in ("write_db", "write_verilog -include_pwr_gnd", "write_def"):
                    if line.startswith(verb + " "):
                        Path(line[len(verb) + 1:].strip("{}")).write_text("derived")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _project(tmp_path, *, gds: bytes | None = LABELLED.encode()):
    project = tmp_path / "project"
    docs = project / "phase1/generated_docs"
    _put(docs / "L8_TIMING_WAVEFORM.json", {"clock_domains": [
        {"role": "primary", "period_ns": 10, "source_pin": "clk"}]})
    _put(docs / "L9_INTEGRATION_SPEC.json", {"top_module": "chip"})
    _put(docs / "L19_CONSTRAINTS_PDK.json", {"fields": {}})
    _put(project / "input/submission_template/tapeout_declaration.json",
         {"answers": {"top_cell": "chip"}})
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "chip.v").write_text("module chip(input clk); endmodule\n")
    pnr = project / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    (pnr / "chip.def").write_text("DESIGN chip ;\nCOMPONENTS 0 ;\n")
    (pnr / "chip_pnr.v").write_text("module chip(); endmodule\n")
    (pnr / "constraint.sdc").write_text("create_clock -period 10 clk\n")
    if gds is not None:
        (pnr / "chip.gds").write_bytes(gds)
    pdk_root = tmp_path / "pdk"
    (pdk_root / PDK).mkdir(parents=True)
    return project, pnr, pdk_root


def _run(tmp_path, monkeypatch, *, gds=LABELLED.encode(), steps=IMAGE_STEPS,
         half="lvs"):
    project, pnr, pdk_root = _project(tmp_path, gds=gds)
    image = Image(tmp_path, steps)
    monkeypatch.setattr(contract, "image_capability", lambda *a, **k: {})
    monkeypatch.setattr(contract, "openroad_home", lambda *a, **k: None)
    monkeypatch.setattr(contract.subprocess, "run", image)
    record = pv.run_half(project, "img", pdk_root, PDK, half, gds=pnr / "chip.gds",
                         routed_def=pnr / "chip.def", netlist=pnr / "chip_pnr.v",
                         sdc=pnr / "constraint.sdc")
    return project, record, image


def _without_gds_extraction():
    steps = json.loads(json.dumps(IMAGE_STEPS))
    del steps["Magic.SpiceExtraction"]["variables"]["MAGIC_EXT_USE_GDS"]
    return steps


# ── THE DEFECT: the LibreLane half read the routed DEF ───────────────────
def test_the_unlabelled_shipped_gds_fails_pin_matching_in_librelane_mode(
        tmp_path, monkeypatch):
    _p, record, image = _run(tmp_path, monkeypatch, gds=UNLABELLED.encode())
    assert image.ran == list(pv.LVS_CHAIN)
    assert record["verdict"] == "FAIL", record
    assert record["metrics"]["design__lvs_unmatched_pin__count"]["value"] == 29


def test_the_extraction_reads_the_shipped_gds_and_the_record_says_so(
        tmp_path, monkeypatch):
    project, record, image = _run(tmp_path, monkeypatch)
    assert record["verdict"] == "PASS", record
    ran = sorted((project / "phase3/librelane/31-lvs").glob(
        "*-magic-spiceextraction/state_out.json"))
    spice = Path(json.loads(ran[-1].read_text())["spice"]).read_text()
    assert "chip.gds" in spice.splitlines()[0], spice
    assert record["scope"]["layout_source"] == "shipped_gds"
    assert record["scope"]["gds_extraction"] == {
        "variable": "MAGIC_EXT_USE_GDS", "declared_by_image": True,
        "image_default": False, "value": True}
    # the overlay is a declared value with its source, like every other
    prov = json.loads((project / "phase3/librelane/31-lvs-config/"
                       "design.provenance.json").read_text())
    assert "shipped GDS" in prov["MAGIC_EXT_USE_GDS"]


def test_the_resolution_records_the_images_declared_variables(tmp_path,
                                                              monkeypatch):
    project, _record, _image = _run(tmp_path, monkeypatch)
    config = project / "phase3/librelane/31-lvs-config/Magic.SpiceExtraction.json"
    declared = contract.declared_variables(config)
    assert declared["MAGIC_EXT_USE_GDS"] is False     # the image's default
    assert json.loads(config.read_text())["MAGIC_EXT_USE_GDS"] is True


def test_an_image_without_gds_extraction_refuses_before_any_tool(tmp_path,
                                                                 monkeypatch):
    with pytest.raises(contract.Refusal, match="LL_PV_GDS_EXTRACTION_UNAVAILABLE"):
        _run(tmp_path, monkeypatch, steps=_without_gds_extraction())


def test_the_drc_half_is_not_touched(tmp_path, monkeypatch):
    project, record, image = _run(tmp_path, monkeypatch, half="drc")
    assert record["verdict"] == "PASS"
    assert image.ran == list(pv.DRC_CHAIN)
    assert all("MAGIC_EXT_USE_GDS" not in r["design"] for r in image.resolved)
    assert "gds_extraction" not in record["scope"]


# ── step 31 on `librelane`: nothing compared is never a pass ──────────────
@pytest.fixture()
def runner(monkeypatch):
    module = importlib.import_module("phase3_one_shot_runner")
    monkeypatch.setattr(module, "_vacuous_on_unrouted", lambda *a, **k: None)
    return module


def _step31(tmp_path, monkeypatch, runner, **kw):
    project, pnr, pdk_root = _project(tmp_path, gds=kw.pop("gds", LABELLED.encode()))
    image = Image(tmp_path, kw.pop("steps", IMAGE_STEPS))
    monkeypatch.setattr(contract, "image_capability", lambda *a, **k: {})
    monkeypatch.setattr(contract, "openroad_home", lambda *a, **k: None)
    monkeypatch.setattr(contract.subprocess, "run", image)
    monkeypatch.setattr(contract, "resolve_image", lambda p: "img")
    monkeypatch.setattr(contract, "resolve_pdk_root", lambda *a, **k: str(pdk_root))
    pdk = SimpleNamespace(name=PDK)
    row = runner._step31_librelane(project, "chip", pdk, "lvs", publish=True)
    vpath = project / "reports/phase3/lvs_verdict.json"
    return project, row, (json.loads(vpath.read_text()) if vpath.is_file() else None)


def test_librelane_mode_publishes_the_shipped_gds_mismatch(tmp_path, monkeypatch,
                                                           runner):
    _p, row, verdict = _step31(tmp_path, monkeypatch, runner,
                               gds=UNLABELLED.encode())
    assert row.status == "FAIL", (row.status, row.detail)
    assert verdict["status"] == "FAIL"
    assert verdict["signoff_layout"] == "shipped_gds"


def test_an_image_without_gds_extraction_is_blocked_never_a_pass(
        tmp_path, monkeypatch, runner):
    _p, row, verdict = _step31(tmp_path, monkeypatch, runner,
                               steps=_without_gds_extraction())
    assert row.status == "NOT_MEASURED", (row.status, row.detail)
    assert row.reason_class == "tool_absent"
    assert "LL_PV_GDS_EXTRACTION_UNAVAILABLE" in row.detail
    assert verdict is not None and verdict["status"] == "BLOCKED", verdict
    assert verdict["finding"] == "LL_PV_GDS_EXTRACTION_UNAVAILABLE"


def test_no_shipped_gds_is_blocked_never_a_pass(tmp_path, monkeypatch, runner):
    _p, row, verdict = _step31(tmp_path, monkeypatch, runner, gds=None)
    assert row.status == "NOT_MEASURED", (row.status, row.detail)
    assert verdict is not None and verdict["status"] == "BLOCKED", verdict
    assert verdict["finding"] == "LL_PV_VIEW_MISSING"


# ── a core-only design (no chip top) resolves at all ─────────────────────
def test_a_core_only_design_hands_the_resolver_its_rtl(tmp_path, monkeypatch):
    """LibreLane's resolver requires VERILOG_FILES of every flow config, and
    `emit_config` declares it only beside a chip top (cmp3 D8, whose control
    `test_no_wrapper_emits_no_verilog_files` stands). So no step-31 config of
    a core-only design resolved: the spm core-only run's step 31 on
    `librelane` died at LL_CONFIG_RESOLUTION_FAILED before comparing anything.
    The half's own overlay hands the build closure by D8's reader."""
    project, _record, image = _run(tmp_path, monkeypatch)
    assert image.resolved[-1]["design"]["VERILOG_FILES"] == [
        "dir::phase2/stage1/rtl/chip.v"]
    prov = json.loads((project / "phase3/librelane/31-lvs-config/"
                       "design.provenance.json").read_text())
    assert "core-only" in prov["VERILOG_FILES"], prov["VERILOG_FILES"]
    # emit_config itself is unchanged: D8's no-wrapper control.
    assert "VERILOG_FILES" not in contract.emit_config(
        project, PDK, tmp_path / "emit.json")
