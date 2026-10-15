"""Regression tests — authorship asserted without ever opening the file.

DEFECT (measured on v1.9.71, on a run where `ip_catalog_query` REFUSED the
catalog match so no reused-IP manifest was written):

`source_manifest_md_emit.collect()` tagged every staged module from ONE
design-level bit — "is there a reused_ip:true manifest?" — and its docstring
claimed the rule "never falsely tags vendor RTL as generated". Measured on that
run's staged tree: 21 of 27 files carried a third-party `SPDX-FileCopyrightText`
header and all 27 were tagged GENERATED. The emitted manifest read

    - Reused from catalog / vendor RTL: 0
    - Authored this run: 27

while 21 of those files name someone else as their author in their own first
twelve lines. `benchmark_verify_report` consumes this manifest, so the false
attribution reached a published report.

The fix lets per-file evidence override the design-level default. It is a LOWER
bound on reuse by construction — a vendored file whose header was stripped is
still indistinguishable from an authored one — and the rendered manifest now
says so instead of asserting a number it cannot support.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, PROGRAMS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Invented module and holder names — they name no design or person in this repo.
_VENDORED = """\
/*
 * widget_core.v : a vendored block
 *
 * SPDX-FileCopyrightText: 2019 A. N. Other <someone@example.invalid>
 * SPDX-License-Identifier: ISC
 */
module widget_core (input wire clk, output wire q);
endmodule
"""

_AUTHORED = """\
// sprocket_top.v
// GENERATED (authored from L1-L9 spec) — chip-level top wrapper.
module sprocket_top (input wire clk, output wire q);
endmodule
"""


def _project(tmp_path: Path) -> Path:
    rtl = tmp_path / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "widget_core.v").write_text(_VENDORED, encoding="utf-8")
    (rtl / "sprocket_top.v").write_text(_AUTHORED, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# 1. NEGATIVE CONTROL — a file that names its own author is not authored here.
# ---------------------------------------------------------------------------
def test_file_with_third_party_copyright_is_not_authored_this_run(tmp_path):
    """NEGATIVE CONTROL: pre-fix both modules were tagged GENERATED because no
    reused-IP manifest existed, and the tally read `Reused: 0 / Authored: 2`.
    This assertion fails against the pre-fix code."""
    m = _load("source_manifest_md_emit")
    tags = {mod: tag for mod, tag, _ in m.collect(_project(tmp_path))}

    assert tags["widget_core"] == "REUSED-IP", (
        "a file whose header carries SPDX-FileCopyrightText names its author; "
        f"it was not authored by this run. got: {tags}")
    assert tags["sprocket_top"] == "GENERATED", (
        f"a file with no copyright header keeps the design-level default; "
        f"got: {tags}")


def test_rendered_tally_counts_the_vendored_file(tmp_path):
    """The tally is the line a reader (and benchmark_verify_report) acts on."""
    m = _load("source_manifest_md_emit")
    md = m.render_md(_project(tmp_path))
    assert "- Reused from catalog / vendor RTL: 1" in md, md
    assert "- Authored this run: 1" in md, md


# ---------------------------------------------------------------------------
# 2. The limit must be stated, not hidden.
# ---------------------------------------------------------------------------
def test_manifest_discloses_that_authored_is_an_upper_bound(tmp_path):
    """A vendored file with a stripped header still counts as authored. Saying
    so is what separates this from the groundless assertion it replaces."""
    m = _load("source_manifest_md_emit")
    md = m.render_md(_project(tmp_path))
    assert "upper bound" in md.lower(), (
        "the manifest must disclose that reuse detection is evidence-limited")


# ---------------------------------------------------------------------------
# 3. PRESERVATION — the design-level rule still governs where it applies.
# ---------------------------------------------------------------------------
def test_reused_manifest_still_tags_everything_reused(tmp_path):
    """When the design IS a reused-IP design, per-file evidence must not
    downgrade anything back to GENERATED."""
    import json
    m = _load("source_manifest_md_emit")
    project = _project(tmp_path)
    (project / "phase2" / "stage1" / "rtl" / m.MANIFEST_NAME).write_text(
        json.dumps({"reused_ip": True}), encoding="utf-8")

    tags = {mod: tag for mod, tag, _ in m.collect(project)}
    assert set(tags.values()) == {"REUSED-IP"}, tags


def test_header_scan_is_bounded_to_the_header(tmp_path):
    """A copyright NOTICE quoted deep in a long file is not that file's own
    header — otherwise this fix trades a false negative for a false positive."""
    m = _load("source_manifest_md_emit")
    rtl = tmp_path / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    body = "\n".join(f"// filler line {i}" for i in range(60))
    (rtl / "quoting_top.v").write_text(
        "// quoting_top.v\n" + body
        + "\n// SPDX-FileCopyrightText: 1999 Someone Else\n"
        + "module quoting_top (input wire clk); endmodule\n",
        encoding="utf-8")

    tags = {mod: tag for mod, tag, _ in m.collect(tmp_path)}
    assert tags["quoting_top"] == "GENERATED", tags


# #3154 public mixed-origin fixture and the existing normal CLI.
import hashlib  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import pytest  # noqa: E402


def _mixed_origin_project(tmp_path, variant="valid"):
    rtl = tmp_path / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    vendor = rtl / "vendor_core.v"
    vendor.write_text("module vendor_core(); endmodule\n")
    name = "different_unit.v" if variant == "renamed" else "authored_wrapper.v"
    module = "joined_unit" if variant == "renamed" else "authored_wrapper"
    wrapper = rtl / name
    wrapper.write_text(f"module {module}(); vendor_core u(); endmodule\n")
    if variant == "copyright":
        wrapper.write_text("// SPDX-FileCopyrightText: 2019 Neutral External Author\n"
                           + wrapper.read_text())
    row = {"path": name, "sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest()}
    if variant == "missing-digest":
        row.pop("sha256")
    elif variant == "stale-digest":
        row["sha256"] = "0" * 64
    elif variant == "missing-file":
        row["path"] = "absent_unit.v"
    manifest = {"reused_ip": True, "ip_list": ["vendor_core"], "ai_authored_files": [row]}
    (rtl / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest) + "\n")
    if variant == "resumed":
        (tmp_path / "provenance.jsonl").write_text(json.dumps({
            "event": "ip_catalog_authored_glue_inventory", "exit_code": 0,
            "run_id": "prior-public-run", "ai_authored_files": [row]}) + "\n")
    return module


def test_issue3154_public_mixed_origin_apis(tmp_path):
    m = _load("source_manifest_md_emit")
    _mixed_origin_project(tmp_path)
    rows = m.collect(tmp_path)
    rendered = m.render_md(tmp_path)
    (tmp_path / "API_RESULT.json").write_text(json.dumps({
        "rows": rows, "rendered": rendered}, indent=2) + "\n")
    assert {mod: tag for mod, tag, _ in rows} == {
        "vendor_core": "REUSED-IP", "authored_wrapper": "GENERATED"}


@pytest.mark.parametrize("variant", ["valid", "missing-digest", "stale-digest",
    "missing-file", "copyright", "resumed", "renamed"])
def test_issue3154_normal_cli_preserves_mixed_source_evidence(tmp_path, variant):
    module = _mixed_origin_project(tmp_path, variant)
    inputs = {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in tmp_path.rglob("*") if p.is_file()}
    argv = [sys.executable, str(PROGRAMS / "source_manifest_md_emit.py"), str(tmp_path)]
    actual = subprocess.run(argv, capture_output=True, text=True)
    (tmp_path / "CLI_RECEIPT.json").write_text(json.dumps({
        "argv": argv, "exit_code": actual.returncode, "stdout": actual.stdout,
        "stderr": actual.stderr, "inputs": inputs}, indent=2) + "\n")
    assert actual.returncode == 0, actual.stdout + actual.stderr
    text = (tmp_path / "SOURCE_MANIFEST.md").read_text()
    rows = _load("source_manifest_md_emit").collect(tmp_path)
    tags = {mod: tag for mod, tag, _ in rows}
    valid = variant in {"valid", "resumed", "renamed"}
    assert tags == {"vendor_core": "REUSED-IP", module: "GENERATED" if valid else "REUSED-IP"}
    assert "- Authored this run: 1" not in text
    assert "current-run authorship is not established" in text.lower()
    if valid:
        assert "- Authored source modules: 1" in text
    else:
        assert "Authorship evidence not used" in text
    assert "absent_unit" not in tags
    assert all(hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
               for name, digest in inputs.items())


def test_issue3154_existing_manifest_is_preserved_until_explicit_force(tmp_path):
    _mixed_origin_project(tmp_path)
    out = tmp_path / "SOURCE_MANIFEST.md"
    original = b"# Retained source history\nOriginal provenance must stay byte-identical.\n"
    out.write_bytes(original)
    argv = [sys.executable, str(PROGRAMS / "source_manifest_md_emit.py"), str(tmp_path)]
    first = subprocess.run(argv, capture_output=True, text=True)
    assert first.returncode == 0
    assert out.read_bytes() == original
    forced = subprocess.run(argv + ["--force"], capture_output=True, text=True)
    assert forced.returncode == 0
    assert "| `authored_wrapper` | GENERATED |" in out.read_text()
