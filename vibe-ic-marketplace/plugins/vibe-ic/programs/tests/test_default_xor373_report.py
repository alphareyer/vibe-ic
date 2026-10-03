"""Report binding controls over unchanged, sealed native evidence; no tools."""
import hashlib
import io
import json
import os
import sys
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _native373_current as native
import _physical_current as pc
import gds_xor_check as xor
import librelane_contract as lc


def _bytes(doc):
    return json.dumps(doc, indent=2).encode()


def _entry(project, path, data):
    return {"path": str(path.relative_to(project)),
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


@pytest.mark.parametrize("scenario", ["normal", "missing", "changed_bytes", "wrong_subject", "result_mismatch"])
def test_ordinary_consumer_binds_actual_xor_report(scenario):
    declared = os.environ.get("VIBEIC_XOR373_NATIVE_PROJECT")
    if not declared:
        pytest.skip("sealed native evidence not supplied: NOT_VERIFIED")
    project = Path(declared).resolve()
    dep_path = project / native.REL
    comparison_path = project / xor.REPORT_REL
    dependency = json.loads(dep_path.read_text())
    folder = project / dependency["folders"][-1]
    report = folder / "xor.xml"
    receipt_path = folder / "vibeic_receipt.json"
    original_paths = (report, receipt_path, dep_path, comparison_path)
    before = {path: pc.digest(path) for path in original_paths}
    original_receipt = json.loads(receipt_path.read_text())
    # Exercise the producer's real, non-native finalization on retained XML.
    # The sealed receipt is never rewritten; only metadata is projected in RAM.
    receipt = lc.bind_xor_report(folder, original_receipt)
    assert receipt["xor_report"]["count"] == dependency["count"] == 0
    assert original_receipt == json.loads(receipt_path.read_text())
    xml = report.read_bytes()
    if scenario in ("wrong_subject", "result_mismatch"):
        root = ET.fromstring(xml)
        if scenario == "wrong_subject":
            root.find("top-cell").text = "wrong_subject"
        else:
            ET.SubElement(root.find("items"), "item")
        xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        # Updated byte identities force the consumer to check parsed semantics.
        receipt["sha256"]["xor.xml"] = hashlib.sha256(xml).hexdigest()
        receipt["xor_report"]["sha256"] = receipt["sha256"]["xor.xml"]
    dependency["current"]["outputs"]["xor_report"] = _entry(project, report, xml)
    projected = {receipt_path: _bytes(receipt)}
    dependency["current"]["outputs"]["producer_2_vibeic_receipt.json"] = _entry(
        project, receipt_path, projected[receipt_path])
    projected[dep_path] = _bytes(dependency)
    comparison = json.loads(comparison_path.read_text())
    comparison["current"]["inputs"]["connectivity"] = _entry(project, dep_path, projected[dep_path])
    comparison["connectivity"]["measurements"][0]["evidence_sha256"] = hashlib.sha256(projected[dep_path]).hexdigest()
    projected[comparison_path] = _bytes(comparison)
    if scenario == "missing":
        projected[report] = None
    elif scenario == "changed_bytes":
        projected[report] = xml + b"\nchanged bytes\n"
    elif scenario in ("wrong_subject", "result_mismatch"):
        projected[report] = xml
    originals = {name: getattr(Path, name) for name in ("open", "stat", "is_file", "read_bytes", "read_text")}
    accesses = []
    def wrapped(method):
        def call(path, *args, **kwargs):
            if path == report:
                accesses.append(method)
            if path not in projected:
                return originals[method](path, *args, **kwargs)
            data = projected[path]
            if data is None:
                if method == "is_file":
                    return False
                raise FileNotFoundError(str(path))
            if method == "is_file":
                return True
            if method == "stat":
                fields = list(originals[method](path, *args, **kwargs))
                fields[6] = len(data)
                return os.stat_result(fields)
            if method == "read_bytes":
                return data
            if method == "read_text":
                return data.decode()
            mode = args[0] if args else kwargs.get("mode", "r")
            return io.BytesIO(data) if "b" in mode else io.StringIO(data.decode())
        return call
    with ExitStack() as stack:
        for method in originals:
            stack.enter_context(patch.object(Path, method, wrapped(method)))
        stack.enter_context(patch.object(lc, "run_container", side_effect=AssertionError("native rerun forbidden")))
        rc, line, _ = xor.judge_receipt(project, xor.REPORT_REL)
        assert rc == (0 if scenario == "normal" else 2), line
        assert accesses
        if scenario in ("wrong_subject", "result_mismatch"):
            connectivity = xor.gds_connectivity(project, comparison["shipped_sha256_live"], comparison)
            assert ("SUBJECT_MISMATCH" if scenario == "wrong_subject" else "COUNT_MISMATCH") in connectivity["reason"]
    assert {path: pc.digest(path) for path in original_paths} == before
    print("XOR_REPORT_OBSERVATION " + json.dumps({
        "scenario": scenario, "gate_rc": rc, "gate_line": line,
        "report_accesses": accesses, "sealed_report_sha256": before[report],
        "native_count": dependency["count"], "native_rerun": False,
        "sealed_files_unchanged": True,
        "normal_binding": "ordinary producer finalization projected in memory"}))
